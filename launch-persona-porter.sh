#!/bin/sh
# Start Persona Porter on Linux:  ./launch-persona-porter.sh
cd "$(dirname "$0")"
if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "Persona Porter needs Python 3.10+ with tkinter. Install it with your package manager, e.g."
  echo "  Debian/Ubuntu: sudo apt install python3-tk"
  echo "  Fedora:        sudo dnf install python3-tkinter"
  echo "  Arch:          sudo pacman -S tk"
  exit 1
fi
exec python3 persona_porter_gui.py "$@"
