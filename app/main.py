"""Dockline's entry point (also PyInstaller's)."""

import sys

from dockline.app import main

if __name__ == '__main__':
    sys.exit(main(sys.argv))
