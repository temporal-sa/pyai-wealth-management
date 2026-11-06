#!/bin/bash
# Run the open-account workflow's three scenarios without the chat or the LLM
# (see run_open_account_workflow.py). Starts a local Python worker in the background
# for the activities (and, without a queue argument, the workflow too), without the
# agents (SKIP_AGENTS=True, so no OpenAI key), and stops it when the scenarios finish.
# Usage: startlocalopenaccount.sh [open-account-task-queue]
# With a task queue name, the workflow runs on that queue: start the Java worker with
# the same name first (java/startlocalworker.sh <queue>).
cd "$(dirname "$0")/../.." || exit 1
[ -f setclaimcheck.sh ] && source ./setclaimcheck.sh
if [ -n "$1" ]; then
  export TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT="$1"
  echo "*** Running the open-account workflow on task queue $1 (the Java worker must poll it). ***"
fi
export SKIP_AGENTS=True
# set the python path up to the src folder to avoid Error while finding module specification
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src"
echo "Starting the worker and running the three scenarios for the open-account workflow."
uv run python -m temporal_supervisor.run_worker &
WORKER_PID=$!
trap 'kill $WORKER_PID 2>/dev/null; wait $WORKER_PID 2>/dev/null' EXIT
uv run python -m temporal_supervisor.run_open_account_workflow
