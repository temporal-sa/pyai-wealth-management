#!/bin/bash
# uses default values if environment variables are not set.
source ../../setclaimcheck.sh
source ../../setoaikey.sh
# set the python path up to the src folder to avoid Error while finding module specification
export PYTHONPATH="${PYTHONPATH}:../../src"
uv run uvicorn api.main:app --reload