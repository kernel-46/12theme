#!/usr/bin/env bash
# Pratyaya — Unix quickstart
set -e

if [ ! -d .venv ]; then
  echo "[pratyaya] creating venv..."
  python3 -m venv .venv
fi
source .venv/bin/activate

pip install -q --disable-pip-version-check -r requirements.txt

echo
echo "[pratyaya] starting on http://localhost:8000"
echo "  - landing  : http://localhost:8000/"
echo "  - agent    : http://localhost:8000/agent"
echo "  - citizen  : http://localhost:8000/citizen"
echo

exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
