#!/bin/bash
# uses default values if environment variables are not set.
source ../../setoaikey.sh
uv run uvicorn api.main:app --reload