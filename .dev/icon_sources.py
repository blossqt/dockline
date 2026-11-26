"""Where each open app's dock icon comes from: its package, its program file, its window, or the blank one.

    python .dev\icon_sources.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from dockline import win  # noqa: E402

for window in win.app_windows(os.getpid()):
    app_id, packaged = win.app_id_of(window)
    if packaged and win.app_id_image(app_id, 64):
        source = 'package logo'
    elif window.path and win.has_own_icon(window.path):
        source = 'program file'
    elif win.window_icon(window.hwnd):
        source = 'window'
    elif app_id and win.app_id_image(app_id, 64):
        source = 'start shortcut'
    else:
        source = 'BLANK'
    print(f'{source:15} {window.title[:40]:40} {app_id or "-":50} {window.path}')
