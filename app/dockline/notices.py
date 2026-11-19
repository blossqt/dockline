"""Windows notifications arriving, so a hidden dock can come out briefly for each new one.

Windows keeps every notification in its own store, an SQLite file (wpndatabase.db), and writes to
it as one arrives. Its official reader (UserNotificationListener) takes 5 to 10 seconds a read here
and gives unpackaged programs no change event, so the store is read directly instead: its files are
looked at once a second (a stat each), and only after they change is the newest toast's id read,
read-only, in a few milliseconds. Nothing in the store is written."""

from __future__ import annotations

import ctypes
import os
import sqlite3
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal

EVERY = 1000          # ms between looks at the store's files

NEWEST = "SELECT MAX(Id) FROM Notification WHERE Type = 'toast'"


_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_INVALID = wintypes.HANDLE(-1).value


class _Info(ctypes.Structure):                   # BY_HANDLE_FILE_INFORMATION (FILETIMEs on 4-byte bounds)
    _pack_ = 4
    _fields_ =[('attributes', wintypes.DWORD), ('created', ctypes.c_uint64), ('accessed', ctypes.c_uint64),
                ('written', ctypes.c_uint64), ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                ('size_low', wintypes.DWORD), ('links', wintypes.DWORD), ('index_high', wintypes.DWORD),
                ('index_low', wintypes.DWORD)]


def _written(path: str):
    """When a file was last written and its size, from the file itself: os.stat may give a directory
    entry's copy, which Windows brings up to date only once a writer closes the file. Opened for its
    attributes alone, sharing everything, so Windows' own use of it is never in the way."""
    handle = _kernel32.CreateFileW(path, 0x80, 0x7, None, 3, 0, None)   # READ_ATTRIBUTES, share all, OPEN_EXISTING
    if handle in (None, _INVALID):
        return None
    try:
        info = _Info()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            return None
        return info.written, (info.size_high << 32) | info.size_low
    finally:
        _kernel32.CloseHandle(handle)


def store_path() -> str:
    return os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Windows', 'Notifications',
                        'wpndatabase.db')


class Notices(QObject):
    """Emits arrived for each look that finds a toast newer than any seen before."""
    arrived = Signal()

    def __init__(self, parent=None, path: str | None = None, every: int = EVERY):
        super().__init__(parent)
        self.path = path or store_path()
        self.stamp = None            # the store's files as last looked at
        self.newest: int | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(every)
        self.timer.timeout.connect(self.look)

    def start(self) -> None:
        self.look()                  # the first look only learns what is there already
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def _stamp(self):
        return tuple(_written(self.path + suffix) for suffix in ('', '-wal'))

    def _read(self) -> int | None:
        if not os.path.exists(self.path):
            return None
        try:
            db = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True, timeout=0.2)
        except sqlite3.Error:
            return None
        try:
            row = db.execute(NEWEST).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except (sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            db.close()

    def look(self) -> None:
        stamp = self._stamp()
        if stamp == self.stamp:
            return
        newest = self._read()
        if newest is None:
            return                   # unreadable now (busy): looked at again next time
        self.stamp = stamp
        if self.newest is not None and newest > self.newest:
            self.arrived.emit()
        self.newest = newest if self.newest is None else max(self.newest, newest)
