"""What would bring a hidden dock out, for N seconds: new toasts in Windows' notification store (with
the app that sent each) and the shell's flash notices (with the window's program and title). Reads
only; prints each with its time.    python .dev\alert_watch.py 180"""
import ctypes
import os
import sqlite3
import sys
import time
from ctypes import wintypes as wt
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer    # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget              # noqa: E402

from dockline.notices import Notices                             # noqa: E402

STORE = r'C:\Users\haoch\AppData\Local\Microsoft\Windows\Notifications\wpndatabase.db'
u, k = ctypes.windll.user32, ctypes.windll.kernel32
app = QApplication([])
start = time.perf_counter()


def stamp():
    return f'{time.perf_counter() - start:7.1f} s'


def program(hwnd):
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k.OpenProcess(0x1000, False, pid.value)
    buf = ctypes.create_unicode_buffer(520)
    n = wt.DWORD(520)
    if h:
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        k.CloseHandle(h)
    title = ctypes.create_unicode_buffer(200)
    u.GetWindowTextW(hwnd, title, 200)
    return f'{os.path.basename(buf.value)} "{title.value[:60]}" visible={bool(u.IsWindowVisible(hwnd))}'


notices = Notices(path=STORE)


def arrived():
    db = sqlite3.connect(f'file:{STORE}?mode=ro', uri=True)
    rows = db.execute("SELECT n.Id, h.PrimaryId, n.ExpiryTime, n.ArrivalTime, length(n.Payload) "
                      "FROM Notification n JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
                      "WHERE n.Type = 'toast' ORDER BY n.Id DESC LIMIT 3").fetchall()
    db.close()
    print(stamp(), 'TOAST', rows[0], flush=True)


notices.arrived.connect(arrived)
notices.start()

hook = QWidget()
hook.setWindowFlags(hook.windowFlags())
hwnd = int(hook.winId())
u.RegisterShellHookWindow(hwnd)
SHELL = u.RegisterWindowMessageW('SHELLHOOK')
NAMES = {0x8006: 'FLASH', 4: 'ACTIVATED', 0x8004: 'RUDEACTIVATED', 6: 'REDRAW'}


class Filter(QAbstractNativeEventFilter):
    def nativeEventFilter(self, kind, message):
        msg = wt.MSG.from_address(int(message))
        if msg.message == SHELL and msg.wParam == 0x8006:
            print(stamp(), 'FLASH', hex(msg.lParam or 0), program(msg.lParam or 0), flush=True)
        return False, 0


f = Filter()
app.installNativeEventFilter(f)
print('watching; newest toast', notices.newest, flush=True)
QTimer.singleShot(int(float(sys.argv[1] if len(sys.argv) > 1 else 180) * 1000), app.quit)
app.exec()
u.DeregisterShellHookWindow(hwnd)
print('done', flush=True)
