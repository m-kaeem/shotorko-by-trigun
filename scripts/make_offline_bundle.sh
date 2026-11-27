#!/usr/bin/env sh
# Run ONCE on a computer WITH internet (same OS + Python version as the target, 3.10-3.13).
# Downloads every Python package Shotorko needs into ./wheels for a later no-internet install.
#   sh scripts/make_offline_bundle.sh
set -e
cd "$(dirname "$0")/.."
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
"$PY" --version
"$PY" -m pip download -r requirements.txt -d wheels
"$PY" -m pip download pip setuptools wheel -d wheels
echo "Done: $(ls wheels | wc -l) files in ./wheels. Copy the project folder, wheels included, to the offline computer."
