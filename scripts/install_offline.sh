#!/usr/bin/env sh
# Run on the OFFLINE computer inside the project folder that contains ./wheels.
# Installs only from ./wheels (--no-index): pip never touches the internet.
#   sh scripts/install_offline.sh      then:  .venv/bin/python run.py
set -e
cd "$(dirname "$0")/.."
[ -d wheels ] || { echo "No ./wheels folder. Run scripts/make_offline_bundle.sh on a computer with internet first."; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheels --upgrade pip
.venv/bin/python -m pip install --no-index --find-links wheels -r requirements.txt
.venv/bin/python -c "import lightgbm, fastapi, pandas; print('Shotorko dependencies OK (installed offline)')"
echo "Start the app: .venv/bin/python run.py   (SHOTORKO_OFFLINE=1 forbids any internet use)"
