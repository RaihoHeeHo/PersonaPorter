#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import tkinter' >/dev/null 2>&1; then
        exec "$candidate" persona_porter_gui.py
    fi
done
print 'Persona Porter needs Python 3.10+ with Tk support.'
print 'On Homebrew, install the python-tk package matching your Python version.'
read '?Press Return to close.'
