#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run_interactive_NL_expert.sh <user> <scenario>
#     user     : youmna|sveta|simon|rama|will|thanh|mohan|test
#     scenario : coffee[2]|putall[2]|cleanall[2]|toast[2]|waterplant[2]

if [[ $# -ne 2 ]]; then
  echo 'Too many/few arguments' >&2
  echo 'First argument is your name: youmna|sveta|simon|rama|will|thanh|mohan|test|callum' >&2
  echo 'Second argument is scenario: coffee[2]|putall[2]|cleanall[2]|toast[2]|waterplant[2]' >&2
  exit 1
fi

case "$1" in
  youmna|sveta|simon|rama|will|thanh|mohan|test|callum) ;;  # ok
  *) echo "Invalid user '$1'." >&2; exit 2 ;;
esac

case "$2" in
  coffee|coffee2)         env="0b87e6e4a25a6750_12669" ;;
  putall|putall2)         env="408f1643f2575996_277"   ;;
  cleanall|cleanall2)     env="6b2e7cbdfe0638e5_2199" ;;
  toast|toast2)           env="d22bd2bc2a63a9e8_1572" ;;
  waterplant|waterplant2) env="a271344397d0507c_5376" ;;
  *) echo "Invalid scenario '$2'." >&2; exit 3 ;;
esac

user="$1"
scenario="$2"
outdir="outputs/${user}_${scenario}_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$outdir"

if [[ ! -d "$outdir" ]]; then
  echo "$outdir already exists but is not a directory" >&2
  exit 4
fi

# Ensure low-latency Python I/O and consistent module path
export PYTHONUNBUFFERED=1
export PYTHONPATH="./src:${PYTHONPATH:-}"

# Launch the interactive runner (unbuffered -u)
exec python -u src/driver/run_interactive.py \
  --use_gt_all \
  --planning_mode=interactive \
  --replan=none \
  --pause_every_subgoal \
  --planning_prompts_path=./src/prompts/interactive/grounded/v1.6.1/ \
  --use_environment="$env" \
  --teach_examples_output="${outdir}/teach_nlu.txt" \
  --llm_api=openai \
  --examples_filename=examples_selected.txt \
  --teach_examples_savemetadata
