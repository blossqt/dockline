"""Apps coming and going on the strip, off screen: presence, the strip's width and the icons' size
frame by frame. python .dev\\strip_moves.py"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['APPDATA'] = tempfile.mkdtemp(prefix='dockline-moves-')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtWidgets import QApplication  # noqa: E402

from dockline import motion, win  # noqa: E402
from dockline.config import Config  # noqa: E402
from dockline.dock import Dock  # noqa: E402

app = QApplication([])
windows = [win.Window(101, 'One', 1, r'C:\a\one.exe'), win.Window(102, 'Two', 2, r'C:\b\two.exe')]
win.app_windows = lambda pid: list(windows)
cfg = Config(None)
cfg['widgets'] = ['start', 'apps', 'clock']
dock = Dock(cfg, testing=True)
dock.show()
strip = next(t for t in dock.tiles if t.ID == 'apps')
strip.refresh()
dock.layout().activate()


def watch(label):
    start = time.perf_counter()
    print(label)
    while time.perf_counter() - start < 0.4:
        app.processEvents()
        time.sleep(0.02)
        entries = strip.display()
        print(f'  {(time.perf_counter() - start) * 1000:5.0f} ms  presence '
              f'{[round(e.presence, 2) for e in entries]}  strip {strip.width()} px  slot {strip.slot:.1f}  '
              f'dock {dock.width()}')
        if not strip.changes:
            break


print('tile height', dock.tile_height)
windows.append(win.Window(103, 'Three', 3, r'C:\c\three.exe'))
strip.refresh()
watch('an app opens:')
windows.pop(0)
strip.refresh()
watch('the first app closes:')
