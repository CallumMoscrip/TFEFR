#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run_interactive_NL_expert.sh <user> <scenario>
#     user     : youmna|sveta|simon|rama|will|thanh|mohan|test
#     scenario : coffee[2]|putall[2]|cleanall[2]|toast[2]|waterplant[2]

if [[ $# -ne 2 ]]; then
  echo 'Too many/few arguments' >&2
  echo 'First argument is your name: youmna|sveta|simon|rama|will|thanh|mohan|test' >&2
  echo 'Second argument is scenario: coffee[2]|putall[2]|cleanall[2]|toast[2]|waterplant[2]' >&2
  exit 1
fi

case "$1" in
  youmna|sveta|simon|rama|will|thanh|mohan|test) ;;  # ok
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

# --- Resolve project root regardless of how/where we’re launched ---
SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

if [[ -d "$SCRIPT_DIR/src" ]]; then
  PROJECT_ROOT="$SCRIPT_DIR"
elif [[ -d "$SCRIPT_DIR/../src" ]]; then
  PROJECT_ROOT="$(readlink -f "$SCRIPT_DIR/..")"
else
  echo "ERROR: cannot locate 'src/' relative to $SCRIPT_DIR" >&2
  exit 10
fi

cd "$PROJECT_ROOT"

# Double-check run_interactive.py exists
if [[ ! -f "$PROJECT_ROOT/src/driver/run_interactive.py" ]]; then
  echo "ERROR: $PROJECT_ROOT/src/driver/run_interactive.py not found" >&2
  exit 11
fi

# Outputs directory anchored to project root (absolute)
outdir="$PROJECT_ROOT/outputs/${user}_${scenario}_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$outdir"

# Low-latency Python I/O and module path
export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"

# Allow override of Python interpreter via env (defaults to python3)
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Use absolute paths for everything that used to be relative
exec "$PYTHON_BIN" -u "$PROJECT_ROOT/src/driver/run_interactive.py" \
  --use_gt_all \
  --planning_mode=interactive \
  --replan=none \
  --pause_every_subgoal \
  --planning_prompts_path="$PROJECT_ROOT/src/prompts/interactive/grounded/v1.6.1/" \
  --use_environment="$env" \
  --teach_examples_output="$outdir/teach_nlu.txt" \
  --llm_api=openai \
  --examples_filename=examples_selected.txt \
  --teach_examples_savemetadata