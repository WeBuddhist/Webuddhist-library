#!/usr/bin/env bash
#
# Deploy the BDRC OpenSearch analysis plugins (analysis-tibetan, analysis-bdrc)
# to an Amazon OpenSearch Service domain as custom ZIP-PLUGIN packages.
#
# It is safe to re-run: existing packages are updated in place, and each AWS
# state transition (upload -> create/update -> AVAILABLE -> associate -> ACTIVE)
# is waited on before the next step. Associations are done one at a time because
# a domain only allows a single associate/dissociate operation at once.
#
# The plugin ZIPs are assumed to already be built and correct.
#
# Usage:
#   scripts/deploy_opensearch_plugins.sh <dev|prod> [plugins-dir]
#
# plugins-dir defaults to "opensearch-plugins" (relative to the repo root) and
# may also be supplied via the PLUGINS_DIR environment variable. Absolute paths
# are used as-is; relative paths are resolved against the repo root.
#
# Per-environment defaults can be overridden with environment variables, e.g.:
#   DOMAIN_NAME=my-domain S3_BUCKET=my-bucket scripts/deploy_opensearch_plugins.sh prod
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
ENVIRONMENT="${1:-}"
if [[ "${ENVIRONMENT}" != "dev" && "${ENVIRONMENT}" != "prod" ]]; then
  echo "Usage: $0 <dev|prod> [plugins-dir]" >&2
  exit 2
fi

# Repo root (this script lives in <repo>/scripts).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Directory holding the plugin ZIPs. Precedence: positional arg 2 > PLUGINS_DIR
# env var > default. Relative paths are resolved against the repo root.
PLUGINS_DIR="${2:-${PLUGINS_DIR:-opensearch-plugins}}"
if [[ "${PLUGINS_DIR}" != /* ]]; then
  PLUGINS_DIR="${REPO_ROOT}/${PLUGINS_DIR}"
fi

# Region is the same for both environments unless overridden.
REGION="${AWS_REGION:-ap-southeast-1}"

# Custom plugins require a Perfect-Forward-Secrecy TLS policy on the domain.
TLS_POLICY="${TLS_POLICY:-Policy-Min-TLS-1-2-PFS-2023-10}"

# Exact engine version the plugin descriptors were built for. AWS requires the
# plugin's opensearch.version to match the domain engine version exactly.
ENGINE_VERSION="${ENGINE_VERSION:-OpenSearch_3.5}"

# S3 key prefix (folder) where the ZIPs are uploaded.
S3_PREFIX="${S3_PREFIX:-os_plugins/}"

# Per-environment defaults (override via env vars).
if [[ "${ENVIRONMENT}" == "dev" ]]; then
  DOMAIN_NAME="${DOMAIN_NAME:-os-dev}"
  S3_BUCKET="${S3_BUCKET:-openpecha-dev}"
else
  DOMAIN_NAME="${DOMAIN_NAME:-os-prod}"
  S3_BUCKET="${S3_BUCKET:-openpecha-prod}"
fi

# Plugins to deploy: "package-name=zip-filename" (filenames live in PLUGINS_DIR).
PLUGINS=(
  "analysis-tibetan=analysis-tibetan.zip"
  "analysis-bdrc=analysis-bdrc.zip"
)

# How long to wait (seconds) for any single state transition before giving up.
WAIT_TIMEOUT="${WAIT_TIMEOUT:-1800}"
POLL_INTERVAL="${POLL_INTERVAL:-20}"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
log()  { echo -e "[$(date '+%H:%M:%S')] $*"; }
die()  { echo -e "[$(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

aws_os() { aws opensearch "$@" --region "${REGION}"; }

# Echo the PackageID for a package name, or empty string if it doesn't exist.
package_id() {
  local name="$1" id
  id="$(aws_os describe-packages \
    --filters "Name=PackageName,Value=${name}" \
    --query 'PackageDetailsList[0].PackageID' --output text 2>/dev/null || true)"
  [[ "${id}" == "None" ]] && id=""
  echo "${id}"
}

package_status() {
  aws_os describe-packages --filters "Name=PackageName,Value=$1" \
    --query 'PackageDetailsList[0].PackageStatus' --output text 2>/dev/null || true
}

package_error() {
  aws_os describe-packages --filters "Name=PackageName,Value=$1" \
    --query 'PackageDetailsList[0].ErrorDetails' --output text 2>/dev/null || true
}

available_version() {
  aws_os describe-packages --filters "Name=PackageName,Value=$1" \
    --query 'PackageDetailsList[0].AvailablePackageVersion' --output text 2>/dev/null || true
}

# DomainPackageStatus for a package on the domain, or "NONE" if not associated.
domain_package_status() {
  local id="$1" status
  status="$(aws_os list-packages-for-domain --domain-name "${DOMAIN_NAME}" \
    --query "DomainPackageDetailsList[?PackageID=='${id}'].DomainPackageStatus | [0]" \
    --output text 2>/dev/null || true)"
  [[ -z "${status}" || "${status}" == "None" ]] && status="NONE"
  echo "${status}"
}

# Version of the package currently deployed on the domain (empty if none).
domain_package_version() {
  local id="$1" v
  v="$(aws_os list-packages-for-domain --domain-name "${DOMAIN_NAME}" \
    --query "DomainPackageDetailsList[?PackageID=='${id}'].PackageVersion | [0]" \
    --output text 2>/dev/null || true)"
  [[ "${v}" == "None" ]] && v=""
  echo "${v}"
}

domain_processing() {
  aws_os describe-domain --domain-name "${DOMAIN_NAME}" \
    --query 'DomainStatus.Processing' --output text 2>/dev/null || echo "true"
}

# Generic poll (bash 3.2 compatible, no associative arrays).
# Usage: poll <description> <expected> <comma-separated-failures> <command...>
# The command is invoked each interval and its stdout compared to <expected>.
poll() {
  local desc="$1" expected="$2" failures="$3"; shift 3
  local elapsed=0 current
  while true; do
    current="$("$@")"
    if [[ "${current}" == "${expected}" ]]; then
      log "  -> ${desc}: ${current}"
      return 0
    fi
    case ",${failures}," in
      *",${current},"*) die "${desc} reached failure state: ${current}" ;;
    esac
    (( elapsed >= WAIT_TIMEOUT )) && die "${desc} timed out after ${WAIT_TIMEOUT}s (last: ${current})"
    log "  ... ${desc}: ${current} (waiting)"
    sleep "${POLL_INTERVAL}"
    (( elapsed += POLL_INTERVAL ))
  done
}

wait_domain_stable() {
  poll "domain '${DOMAIN_NAME}' processing" "False" "" domain_processing
}

wait_package_available() {
  poll "package '$1' status" "AVAILABLE" "VALIDATION_FAILED,COPY_FAILED,DELETE_FAILED" \
    package_status "$1"
}

# --------------------------------------------------------------------------- #
# Pre-flight
# --------------------------------------------------------------------------- #
command -v aws >/dev/null || die "aws CLI not found on PATH"

log "Environment : ${ENVIRONMENT}"
log "Plugins dir : ${PLUGINS_DIR}"
log "Domain      : ${DOMAIN_NAME}"
log "Region      : ${REGION}"
log "S3 bucket   : ${S3_BUCKET}"
log "S3 prefix   : ${S3_PREFIX}"
log "Engine ver. : ${ENGINE_VERSION}"
log "TLS policy  : ${TLS_POLICY}"

aws_os describe-domain --domain-name "${DOMAIN_NAME}" >/dev/null \
  || die "domain '${DOMAIN_NAME}' not found in ${REGION} (check name/credentials)"

# Verify the domain engine version matches what the plugins were built for.
CURRENT_ENGINE="$(aws_os describe-domain --domain-name "${DOMAIN_NAME}" \
  --query 'DomainStatus.EngineVersion' --output text)"
if [[ "${CURRENT_ENGINE}" != "${ENGINE_VERSION}"* ]]; then
  die "domain engine '${CURRENT_ENGINE}' does not match expected '${ENGINE_VERSION}'. \
Rebuild the plugins for '${CURRENT_ENGINE}' or set ENGINE_VERSION accordingly."
fi
log "Domain engine version confirmed: ${CURRENT_ENGINE}"

# --------------------------------------------------------------------------- #
# 1. Ensure the domain uses a PFS TLS policy (required for custom plugins)
# --------------------------------------------------------------------------- #
CURRENT_TLS="$(aws_os describe-domain --domain-name "${DOMAIN_NAME}" \
  --query 'DomainStatus.DomainEndpointOptions.TLSSecurityPolicy' --output text 2>/dev/null || echo "")"
if [[ "${CURRENT_TLS}" != "${TLS_POLICY}" ]]; then
  log "Updating TLS policy: ${CURRENT_TLS:-<none>} -> ${TLS_POLICY}"
  aws_os update-domain-config --domain-name "${DOMAIN_NAME}" \
    --domain-endpoint-options "TLSSecurityPolicy=${TLS_POLICY}" >/dev/null
  wait_domain_stable
else
  log "TLS policy already '${TLS_POLICY}'"
fi

# --------------------------------------------------------------------------- #
# 2. Upload ZIPs and create/update packages, then wait for AVAILABLE
# --------------------------------------------------------------------------- #
for entry in "${PLUGINS[@]}"; do
  name="${entry%%=*}"
  zip_file="${entry#*=}"
  zip_path="${PLUGINS_DIR}/${zip_file}"
  s3_key="${S3_PREFIX}${name}.zip"

  [[ -f "${zip_path}" ]] || die "plugin ZIP not found: ${zip_path}"

  log "=== ${name} ==="
  log "Uploading ${zip_path} -> s3://${S3_BUCKET}/${s3_key}"
  aws s3 cp "${zip_path}" "s3://${S3_BUCKET}/${s3_key}" --region "${REGION}" >/dev/null

  id="$(package_id "${name}")"
  status="$(package_status "${name}")"

  if [[ -z "${id}" ]]; then
    log "Creating package ${name}"
    id="$(aws_os create-package --package-name "${name}" --package-type ZIP-PLUGIN \
      --engine-version "${ENGINE_VERSION}" \
      --package-source "S3BucketName=${S3_BUCKET},S3Key=${s3_key}" \
      --query 'PackageDetails.PackageID' --output text)"
  elif [[ "${status}" == "VALIDATION_FAILED" || "${status}" == "COPY_FAILED" ]]; then
    # A failed package can't always be updated; if it isn't attached, recreate it.
    if [[ "$(domain_package_status "${id}")" == "NONE" ]]; then
      log "Package ${name} is ${status} and unassociated; deleting and recreating"
      aws_os delete-package --package-id "${id}" >/dev/null
      while [[ -n "$(package_id "${name}")" ]]; do log "  ... waiting for delete"; sleep "${POLL_INTERVAL}"; done
      id="$(aws_os create-package --package-name "${name}" --package-type ZIP-PLUGIN \
        --engine-version "${ENGINE_VERSION}" \
        --package-source "S3BucketName=${S3_BUCKET},S3Key=${s3_key}" \
        --query 'PackageDetails.PackageID' --output text)"
    else
      log "Package ${name} is ${status} but associated; pushing update"
      aws_os update-package --package-id "${id}" \
        --package-source "S3BucketName=${S3_BUCKET},S3Key=${s3_key}" >/dev/null
    fi
  else
    # Package exists and is healthy: push the (possibly new) ZIP as an update.
    log "Package ${name} exists (${status}); pushing update from S3"
    aws_os update-package --package-id "${id}" \
      --package-source "S3BucketName=${S3_BUCKET},S3Key=${s3_key}" >/dev/null
  fi

  log "Package id: ${id}"
  wait_package_available "${name}"
done

# --------------------------------------------------------------------------- #
# 3. Associate each package with the domain (serially), wait for ACTIVE
# --------------------------------------------------------------------------- #
for entry in "${PLUGINS[@]}"; do
  name="${entry%%=*}"
  id="$(package_id "${name}")"
  [[ -n "${id}" ]] || die "package '${name}' not found when associating"

  log "=== associating ${name} (${id}) ==="
  wait_domain_stable

  dstatus="$(domain_package_status "${id}")"
  avail_ver="$(available_version "${name}")"
  domain_ver="$(domain_package_version "${id}")"

  if [[ "${dstatus}" == "ACTIVE" && "${domain_ver}" == "${avail_ver}" ]]; then
    log "  -> already ACTIVE at version ${domain_ver}; nothing to do"
    continue
  fi

  log "Associating ${name} (domain status: ${dstatus}, domain ver: ${domain_ver:-none}, available ver: ${avail_ver})"
  aws_os associate-package --package-id "${id}" --domain-name "${DOMAIN_NAME}" >/dev/null

  # Wait for this association to finish before starting the next one.
  poll "package '${name}' on domain" "ACTIVE" "ASSOCIATION_FAILED,DISSOCIATION_FAILED" \
    domain_package_status "${id}"
  wait_domain_stable
done

log "All plugins are AVAILABLE and ACTIVE on '${DOMAIN_NAME}'."
log "Next: with writers paused, rebuild both search indexes against this environment:"
log "    python -m scripts.reindex --writes-paused"
