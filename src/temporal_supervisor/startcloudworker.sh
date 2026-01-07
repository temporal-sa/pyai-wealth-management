#!/bin/bash
source ../../setoaikey.sh
source ../../setcloudenv.sh
uv run python -m temporal_supervisor.run_worker
