"""Pictures the running Dockline's bar from the screen (read only): its whole window and each end
enlarged 8x, into .dev\\out\\bar_*.png, and prints where its windows are.
    python .dev\\bar_shot.py [name]"""
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

from PySide6.QtCore import QRect, Qt           # noqa: E402
from PySide6.QtGui import QGuiApplication      # noqa: E402

u, k = ctypes.windll.user32, ctypes.windll.kernel32
app = QGuiApplication([])
out = Path(__file__).with_name('out')
out.mkdir(exist_ok=True)
name = sys.argv[1] if len(sys.argv) > 1 else 'bar'


def dockline_windows():
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k.OpenProcess(0x1000, False, pid.value)
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        if h:
            k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
            k.CloseHandle(h)
        if os.path.basename(buf.value).lower() == 'dockline.exe' and u.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            title = ctypes.create_unicode_buffer(100)
            u.GetWindowTextW(hwnd, title, 100)
            found.append((hwnd, title.value, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True

    u.EnumWindows(each, 0)
    return found


wins = dockline_windows()
for w in wins:
    print(hex(w[0]), repr(w[1]), w[2])
bar = max((w for w in wins if w[1] == 'Dockline'), key=lambda w: w[2][2] * w[2][3], default=None)
if bar is None:
    sys.exit('no bar on screen')
x, y, w, h = bar[2]
screen = QGuiApplication.screenAt(QRect(x, y, w, h).center()) or QGuiApplication.primaryScreen()
g = screen.geometry()
ratio = screen.devicePixelRatio()
pad = 12
shot = screen.grabWindow(0, int((x - pad) / ratio - g.x()) if False else x - pad - round(g.x() * ratio) if ratio == 1 else x - pad,
                         y - pad, w + 2 * pad, h + 2 * pad)
shot.save(str(out / f'{name}_whole.png'))
side = h + 2 * pad
left = shot.copy(0, 0, side, side).scaled(side * 8, side * 8, Qt.KeepAspectRatio, Qt.FastTransformation)
right = shot.copy(shot.width() - side, 0, side, side).scaled(side * 8, side * 8, Qt.KeepAspectRatio,
                                                               Qt.FastTransformation)
left.save(str(out / f'{name}_left.png'))
right.save(str(out / f'{name}_right.png'))
print('saved', shot.width(), 'x', shot.height(), 'ratio', ratio)
