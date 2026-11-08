#!/usr/bin/env bash
# Oeffnet das ClipBot-Dashboard im Browser. Lauf ohne Oberflaeche: .venv/bin/python run.py
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Erste Einrichtung, das dauert einen Moment..."
  PY=python3; for c in python3.12 python3.13 python3.11; do command -v $c >/dev/null && PY=$c && break; done
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
fi
.venv/bin/python -m pip install -q --upgrade -r requirements.txt
.venv/bin/python app.py "$@"
