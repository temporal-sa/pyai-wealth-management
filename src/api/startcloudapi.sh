#!/bin/bash
source ../../setoaikey.sh
source ../../setcloudenv.sh
uv run uvicorn api.main:app --reload