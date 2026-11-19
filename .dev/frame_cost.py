"""How long one frame of an app coming or going takes on the window thread (the dock refits every
frame meanwhile). python .dev\frame_cost.py"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['APPDATA'] = tempfile.mkdtemp(prefix='dockline-cost-')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtWidgets import QApplication  # noqa: E402

from dockline import motion, win  # noqa: E402
from dockline.config import Config  # noqa: E402
from dockline.dock import Dock  # noqa: E402

motion.on = lambda: True
app = QApplication([])
windows = [win.Window(100 + i, f'App {i}', i, rf'C:\apps\app{i}.exe') for i in range(12)]
win.app_windows = lambda pid: list(windows)
cfg = Config(None)
cfg['widgets'] = ['start', 'apps', 'cpu', 'ram', 'clock', 'ai_limits']
dock = Dock(cfg, testing=True)
dock.show()
strip = next(t for t in dock.tiles if t.ID == 'apps')
strip.refresh()
times, own, paint = [], [], []
for round_ in range(20):
    windows.append(win.Window(500 + round_, 'New', 50, rf'C:\new\n{round_}.exe'))
    strip.refresh()
    while strip.changes:
        start = time.perf_counter()
        strip.frame()
        middle = time.perf_counter()
        app.processEvents()
        end = time.perf_counter()
        times.append((end - start) * 1000)
        own.append((middle - start) * 1000)
        paint.append((end - middle) * 1000)
        time.sleep(0.004)
    windows.pop()
    strip.refresh()
    while strip.changes:
        start = time.perf_counter()
        strip.frame()
        middle = time.perf_counter()
        app.processEvents()
        end = time.perf_counter()
        times.append((end - start) * 1000)
        own.append((middle - start) * 1000)
        paint.append((end - middle) * 1000)
        time.sleep(0.004)
for name, values in (('whole frame', times), ('frame() itself', own), ('drawing after', paint)):
    values.sort()
    print(f'{name}: {len(values)} frames, median {values[len(values) // 2]:.2f} ms, '
          f'95th {values[int(len(values) * 0.95)]:.2f} ms, worst {values[-1]:.2f} ms')
dock.ai.stop()
