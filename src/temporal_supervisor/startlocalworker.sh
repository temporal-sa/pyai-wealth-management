#!/bin/bash
source ../../setoaikey.sh
uv run python -m temporal_supervisor.run_worker
