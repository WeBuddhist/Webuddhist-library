# Source this file. Needs the AWS CLI and jq.

# Run a script on an instance over SSM and wait for it, printing its output (no 100 s waiter limit).
ssm_run() { # <instance> <script file> [args...]
  local instance=$1 file=$2 cmd status id params
  shift 2
  # Write the script to a file first: with `bash -s`, any child that reads stdin would eat the script.
  cmd="f=\$(mktemp) && cat >\"\$f\" <<'SSM_RUN_SCRIPT'"$'\n'"$(cat "$file")"$'\n'"SSM_RUN_SCRIPT"$'\n'
  cmd+="bash \"\$f\" $(printf '%q ' "$@"); rc=\$?; rm -f \"\$f\"; exit \$rc"
  params=$(mktemp)
  jq -n --arg c "$cmd" '{commands: [$c], executionTimeout: ["3600"]}' >"$params"
  id=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --parameters "file://$params" --query Command.CommandId --output text)
  rm -f "$params"
  echo "[$(date -u +%H:%M:%S)] SSM $id on $instance: $(basename "$file") $*"
  for _ in $(seq 1 400); do
    sleep 10
    status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
      --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in Pending | InProgress | Delayed) continue ;; *) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
    --query '[StandardOutputContent, StandardErrorContent]' --output text || true
  [ "$status" = "Success" ] || { echo "::error::SSM command on $instance ended with status $status"; exit 1; }
}
