#!/bin/zsh
# Add the tradition and Chants tags in final/tag_plan.tsv to the library. DRY RUN BY DEFAULT. ADD-ONLY.
#
#   BASE=https://library-prod.webuddhist.com zsh apply_tag_plan.sh                 # dry run: reads only, prints every change
#   BASE=https://library-prod.webuddhist.com zsh apply_tag_plan.sh --apply         # adds the tags, after you type APPLY
#   options: --only <Mahayana|Vajrayana|Theravada|Chants>   only lines for that tag
#            --plan <file>                                  another plan file (default final/tag_plan.tsv)
#
# Tags live on the WORK: one line on a root text tags that root and all its translations.
# Safety:
# - EXISTING TAGS ARE NEVER TOUCHED. The script only adds (POST); it has no remove or replace. A plan line with any action
#   other than "add" stops the script before anything happens.
# - Dry run sends only GET requests. --apply sends one POST per planned addition, nothing else.
# - Safe to rerun: a tag already present is skipped (and the API MERGEs, so it can never be duplicated).
# - An addition that would give a text a second tradition tag is BLOCKED, and then nothing is applied.
# - Before any change it checks that every tag in the plan exists for the application and every text exists.
# - After each addition it re-reads the text and checks the tag is there and every earlier tag is still there.
# - The API key comes from LIBRARY_API_KEY or a hidden prompt; it is never printed or saved.
set -u
cd "${0:A:h}"
BASE="${BASE:?Set BASE first, for example: BASE=https://library-prod.webuddhist.com zsh apply_tag_plan.sh}"
APP=webuddhist
TRADITION_IDS=(296WZOr8MXWteSqBBI7pl zjHEBN2egpftHD7BRMQko ZPcgMZgVJxAMQ7rpfW72a)
PLAN_FILE=final/tag_plan.tsv; ONLY=""; APPLY=0
while (( $# )); do
  case $1 in
    --apply) APPLY=1 ;;
    --only) ONLY=$2; shift ;;
    --plan) PLAN_FILE=$2; shift ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
  shift
done
[[ -r $PLAN_FILE ]] || { echo "Plan file $PLAN_FILE not found."; exit 1; }

# ---- read the plan (before asking for a key, so a bad plan never reaches the server)
typeset -a ID TAG TNAME
while IFS=$'\t' read -r a i g n rest; do
  [[ -z $a || $a == \#* ]] && continue
  [[ $a == add ]] || { echo "Plan line '$a $i $n' is not an addition. This script never removes or changes existing tags. Stopping; nothing was sent."; exit 1; }
  [[ $i =~ '^[A-Za-z0-9_-]{21}$' && $g =~ '^[A-Za-z0-9_-]{21}$' ]] || { echo "Invalid plan line: $a $i $g. Stopping."; exit 1; }
  [[ -n $ONLY && $n != $ONLY ]] && continue
  ID+=($i); TAG+=($g); TNAME+=($n)
done < $PLAN_FILE

KEY=${LIBRARY_API_KEY:-}
if [[ -z $KEY ]]; then printf 'API key: '; read -rs KEY; echo; fi
[[ -n $KEY ]] || { echo "No API key entered."; exit 1; }
H=(-H "X-API-Key: $KEY" -H "X-Application: $APP")
LOG=final/apply_log_$(date +%Y%m%d_%H%M%S)_$( (( APPLY )) && echo apply || echo dryrun ).txt
say() { print -r -- "$*" | tee -a $LOG; }

tags_of() {  # prints the tag_ids of a text, comma separated, or ERR <code>
  local body code
  body=$(curl -sS -w $'\n%{http_code}' "$BASE/v2/texts/$1" "${H[@]}")
  code=${body##*$'\n'}; body=${body%$'\n'*}
  [[ $code == 200 ]] || { print -r -- "ERR $code"; return; }
  body=${${body#*\"tag_ids\":\[}%%\]*}
  print -r -- ${body//\"/}
}

say "Plan $PLAN_FILE${ONLY:+ (only $ONLY)}: ${#ID} additions. Mode: $( (( APPLY )) && echo APPLY || echo 'DRY RUN (no changes)'). Server: $BASE"
(( ${#ID} )) || exit 0

alltags=$(curl -sS -w $'\n%{http_code}' "$BASE/v2/tags" "${H[@]}") || { say "Could not read tags. Stopping."; exit 1; }
[[ ${alltags##*$'\n'} == 200 ]] || { say "Reading tags failed with HTTP ${alltags##*$'\n'} (check the API key and BASE). Stopping."; exit 1; }
alltags=${alltags%$'\n'*}
for g in ${(u)TAG}; do
  [[ $alltags == *"\"id\":\"$g\""* ]] || { say "Tag $g is not a $APP tag on $BASE. Stopping."; exit 1; }
done

# ---- check against the live state: what would be added
typeset -A STATE
todo=(); n_skip=0; n_block=0; n_err=0
for k in {1..${#ID}}; do
  i=$ID[k]; g=$TAG[k]
  if [[ -z ${STATE[$i]+x} ]]; then STATE[$i]=$(tags_of $i); fi
  cur=$STATE[$i]
  if [[ $cur == ERR* ]]; then say "ERROR   $i: HTTP ${cur#ERR }"; n_err=$((n_err + 1)); continue; fi
  if [[ ,$cur, == *,$g,* ]]; then say "SKIP    add $TNAME[k] on $i: already there"; n_skip=$((n_skip + 1)); continue; fi
  if (( ${TRADITION_IDS[(Ie)$g]} )); then
    for t in $TRADITION_IDS; do
      if [[ ,$cur, == *,$t,* ]]; then say "BLOCKED add $TNAME[k] on $i: it already has a tradition tag ($t)"; n_block=$((n_block + 1)); continue 2; fi
    done
  fi
  STATE[$i]=${cur:+$cur,}$g
  say "WOULD   add $TNAME[k] on $i"
  todo+=($k)
done
say "${#todo} additions, $n_skip already done, $n_block blocked, $n_err errors."
(( APPLY )) || { say "Dry run only. Nothing was changed. Log: $LOG"; exit 0; }
(( ${#todo} )) || exit 0
(( n_err + n_block == 0 )) || { say "Fix the errors or blocked lines first. Nothing was changed."; exit 1; }
printf 'Type APPLY to add these %d tags: ' ${#todo}; read -r answer
[[ $answer == APPLY ]] || { say "Stopped. Nothing was changed."; exit 1; }

failed=0
for k in $todo; do
  i=$ID[k]; g=$TAG[k]
  before=$(tags_of $i)
  [[ ,$before, == *,$g,* ]] && { say "SKIP    $i: $TNAME[k] already there"; continue; }
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/v2/texts/$i/tags/$g" "${H[@]}")
  after=$(tags_of $i)
  ok=1
  [[ $code == 204 && ,$after, == *,$g,* ]] || ok=0
  for old in ${(s:,:)before}; do [[ ,$after, == *,$old,* ]] || ok=0; done
  if (( ok )); then say "OK      add $TNAME[k] on $i"; else say "FAIL    add $TNAME[k] on $i (HTTP $code)"; failed=$((failed + 1)); fi
done
say "Failed: $failed. Log: $LOG"
(( failed == 0 ))
