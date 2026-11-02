#!/bin/bash
# Launch the home screen; each tool starts only when you open it.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Run Setup.command first to install the Python environment."
  read -r -p "Press Enter to close..."
  exit 1
fi
exec .venv/bin/python main.py "$@"
