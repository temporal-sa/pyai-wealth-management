#!/bin/bash
# uses default values if environment variables are not set.
uv run uvicorn api.main:app --reload