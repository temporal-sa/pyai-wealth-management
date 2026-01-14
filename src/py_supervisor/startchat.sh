#!/bin/bash
source ../../setoaikey.sh
# set the python path up to the src folder to avoid Error while finding module specification
export PYTHONPATH="${PYTHONPATH}:../../src"
uv run python -m py_supervisor.main