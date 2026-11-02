#!/bin/bash
source ../../setclaimcheck.sh
source ../../setoaikey.sh
# if you pass in a parameter, use that as the task queue
# for the Open Account workflow
source ../../setsplitworkflows.sh $1
# set the python path up to the src folder to avoid Error while finding module specification
export PYTHONPATH="${PYTHONPATH}:../../src"
uv run python -m temporal_supervisor.run_worker
