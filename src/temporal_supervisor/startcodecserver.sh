#!/bin/bash
echo "****************************************"
echo "* Remember to start your Redis Server! *"
echo "****************************************"
source ../../setclaimcheck.sh
# set the python path up to the src folder to avoid Error while finding module specification
export PYTHONPATH="${PYTHONPATH}:../../src"
uv run python -m codec_server.codec_server
