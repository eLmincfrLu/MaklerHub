#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo " MaklerHub hazırlanır..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r requirements.txt
( sleep 2; (command -v open >/dev/null && open http://127.0.0.1:5000) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:5000) || true ) &
python run.py "$@"
