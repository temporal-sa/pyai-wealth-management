#!/bin/bash
if [ -z "$1" ]; then
   echo "You must specify a workflow ID"
   exit 1
fi
source ../../setclaimcheck.sh
source ../../setcloudenv.sh
export SKIP_OPENAI_PLUGIN=True; 
export PYTHONPATH="${PYTHONPATH}:../../src"; 
uv run python -m temporal_supervisor.run_send_compliance_approval --workflow-id "$1"
