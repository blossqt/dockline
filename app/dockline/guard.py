"""The watcher: a small process (no window, no Qt) started with the dock. It waits for the dock's
process to end, and if it ended without quitting - ended in Task Manager, or crashed - gives the
Windows taskbar and the screen band back, as quitting would have."""

from __future__ import annotations

from . import win
from .config import Config
from .taskbar import Taskbar


def guard(pid: int, hwnd: int) -> int:
    if not win.wait_for_exit(pid):
        return 1
    if hwnd:
        win.remove_app_bar(hwnd)
    from .veil import any_panel, clear_taskbars, set_alpha
    clear_taskbars(False)
    if any_panel():
        set_alpha(any_panel(), 255)          # ended while the panel was being placed                         # ended while the tray was open: the taskbar is made opaque
    cfg = Config.load()
    if cfg['taskbar_saved'] is not None:          # still hidden: the dock never got to restore it
        Taskbar(cfg).restore()
    else:
        win.show_taskbars(True)
    return 0
