#!/usr/bin/env bash
# Oeffnet das ClipBot-Dashboard im Browser. Lauf ohne Oberflaeche: .venv/bin/python run.py
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Erste Einrichtung, das dauert einen Moment..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
fi
.venv/bin/python -m pip install -q --upgrade -r requirements.txt
.venv/bin/python app.py "$@"
