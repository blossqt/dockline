"""Watches this PC's real notification store with Dockline's own Notices for N seconds and prints each
arrival with the newest toast's app and id. Reads only.    python .dev\notice_watch.py 60"""
import os
import sqlite3
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QTimer              # noqa: E402
from PySide6.QtWidgets import QApplication     # noqa: E402

from dockline.notices import Notices           # noqa: E402

STORE = r'C:\Users\haoch\AppData\Local\Microsoft\Windows\Notifications\wpndatabase.db'
app = QApplication([])
started = time.perf_counter()
notices = Notices(path=STORE)


def arrived():
    db = sqlite3.connect(f'file:{STORE}?mode=ro', uri=True)
    row = db.execute("SELECT n.Id, h.PrimaryId FROM Notification n JOIN NotificationHandler h "
                     "ON h.RecordId = n.HandlerId WHERE n.Type = 'toast' ORDER BY n.Id DESC LIMIT 1").fetchone()
    db.close()
    print(f'{time.perf_counter() - started:6.1f} s  arrived: id {row[0]} from {row[1]}', flush=True)


notices.arrived.connect(arrived)
notices.start()
print(f'watching; newest toast id {notices.newest}', flush=True)
QTimer.singleShot(int(float(sys.argv[1] if len(sys.argv) > 1 else 60) * 1000), app.quit)
app.exec()
print('done', flush=True)
