#!/bin/bash
# Opens in preview mode; files move only after you click Sort checked files.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Run Setup.command first to install the Python environment."
  read -r -p "Press Enter to close..."
  exit 1
fi
exec .venv/bin/python bounce_sorter_app.py
