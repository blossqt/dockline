"""Opening the notification area's hidden icons without the Windows taskbar showing.

Windows fills that panel in only while its taskbar is up. Cloaking the taskbar from another program
is refused ("access denied"), and a picture held over it still let it flash through. What works:
the taskbar is made a fully see-through layered window (alpha 0) for the moment it is up - it comes
up invisible, its panel (a window of its own) opens as usual, and once the panel closes the taskbar
is hidden again and made as it was. The watcher process and Taskbar.restore() undo the see-through
state too, so a crash cannot leave an invisible taskbar behind.
"""

from __future__ import annotations

import ctypes
import time

from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer

from . import uia, win

HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x2, 0x1, 0x10
win.user32.SetWindowPos.argtypes = [win.HWND, win.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint]


def raise_top(hwnd: int) -> None:
    win.user32.SetWindowPos(hwnd, win.HWND(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def panel_window() -> int:
    """The hidden-icons panel's window while it is open, else 0."""
    found = []

    def each(hwnd, _):
        if win._class_name(hwnd) in ('TopLevelWindowForOverflowXamlIsland', 'NotifyIconOverflowWindow')                 and win.user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    win.user32.EnumWindows(win.WNDENUMPROC(each), 0)
    return found[0] if found else 0


def panel_open() -> bool:
    found = []

    def each(hwnd, _):
        if win._class_name(hwnd) in ('TopLevelWindowForOverflowXamlIsland', 'NotifyIconOverflowWindow') \
                and win.user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    win.user32.EnumWindows(win.WNDENUMPROC(each), 0)
    return bool(found)


LAYERED, LWA_ALPHA = 0x80000, 0x2
win.user32.SetLayeredWindowAttributes.argtypes = [win.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
win.user32.GetLayeredWindowAttributes.argtypes = [win.HWND, ctypes.POINTER(wintypes.DWORD),
                                                  ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]


def clear_taskbars(clear: bool) -> None:
    """Make every taskbar fully see-through, or opaque and not layered again."""
    for hwnd in win.taskbars():
        ex = win.user32.GetWindowLongPtrW(hwnd, win.GWL_EXSTYLE)
        if clear:
            win.user32.SetWindowLongPtrW(hwnd, win.GWL_EXSTYLE, ex | LAYERED)
            win.user32.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)
        elif ex & LAYERED:
            alpha = ctypes.c_ubyte(255)
            flags = wintypes.DWORD()
            key = wintypes.DWORD()
            win.user32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags))
            if alpha.value == 0:                     # ours: see-through. (Leave any other program's alone.)
                win.user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
                win.user32.SetWindowLongPtrW(hwnd, win.GWL_EXSTYLE, ex & ~LAYERED)


def set_alpha(hwnd: int, alpha: int) -> None:
    """A window made see-through (0) or opaque again (255, and no longer layered)."""
    ex = win.user32.GetWindowLongPtrW(hwnd, win.GWL_EXSTYLE)
    if alpha >= 255:
        if ex & LAYERED:
            win.user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
            win.user32.SetWindowLongPtrW(hwnd, win.GWL_EXSTYLE, ex & ~LAYERED)
        return
    win.user32.SetWindowLongPtrW(hwnd, win.GWL_EXSTYLE, ex | LAYERED)
    win.user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)


def any_panel() -> int:
    """The hidden-icons panel's window, shown or not (it exists while closed), else 0."""
    found = []

    def each(hwnd, _):
        if win._class_name(hwnd) in ('TopLevelWindowForOverflowXamlIsland', 'NotifyIconOverflowWindow'):
            found.append(hwnd)
            return False
        return True

    win.user32.EnumWindows(win.WNDENUMPROC(each), 0)
    return found[0] if found else 0


SWP_NOZORDER = 0x4
PLACING = 0.35          # seconds the panel is held in place over the Tray button while Windows settles it


class Veil(QObject):
    """One opening of the hidden icons, over the dock's Tray button: the panel is invisible from the
    moment it opens until it has been moved there, so it never shows beside the clock first. With
    the Windows taskbar hidden, the taskbar also comes up invisible for the panel to fill in."""

    def __init__(self, dock, taskbar_up: bool = False):
        super().__init__(dock)
        self.dock = dock
        self.taskbar_up = taskbar_up             # the taskbar shows already: only the panel to press
        self.started = time.monotonic()
        self.pressed = False                     # the panel's button has been pressed
        self.seen_open = False
        self.placed_at = None                    # when the panel was first seen open
        self.panel = any_panel()
        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self.step)

    def isVisible(self) -> bool:
        return True

    def start(self) -> None:
        if self.panel:
            set_alpha(self.panel, 0)             # unseen until it is above the Tray button
        if self.taskbar_up:
            self.pressed = uia.open_hidden_icons()
            if not self.pressed:
                self.finish()
                return
        else:
            clear_taskbars(True)
            self.dock.taskbar.hold(30.0)         # the dock's keeper leaves the taskbar alone meanwhile
            win.show_taskbars(True)
            win.press(win.VK_LWIN, ord('B'))     # up it comes, invisible, with its tray in reach
        self.ticker.start(10)

    def target(self, panel: int) -> tuple[int, int] | None:
        """Where the panel goes: centred over the Tray button, just clear of the dock (physical px)."""
        tile = next((t for t in self.dock.tiles if t.ID == 'tray'), None)
        anchor = tile if tile is not None else self.dock
        screen = self.dock.screen() or self.dock.target_screen
        ratio = screen.devicePixelRatio()
        rect = wintypes.RECT()
        win.user32.GetWindowRect(panel, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        from PySide6.QtCore import QPoint
        centre = anchor.mapToGlobal(QPoint(anchor.width() // 2, 0))
        dock_top = self.dock.mapToGlobal(QPoint(0, 0)).y()
        g = screen.geometry()
        x = round(centre.x() * ratio - width / 2)
        x = max(round(g.left() * ratio) + 8, min(x, round((g.left() + g.width()) * ratio) - width - 8))
        gap = round(10 * ratio)
        if self.dock.edge == 'top':
            y = round((dock_top + self.dock.height()) * ratio) + gap
        else:
            y = round(dock_top * ratio) - height - gap
        return x, y

    def place_panel(self, panel: int) -> None:
        spot = self.target(panel)
        if spot is None:
            return
        rect = wintypes.RECT()
        win.user32.GetWindowRect(panel, ctypes.byref(rect))
        if (rect.left, rect.top) != spot:
            win.user32.SetWindowPos(panel, None, spot[0], spot[1], 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

    def step(self) -> None:
        now = time.monotonic() - self.started
        if not self.pressed:
            if uia.open_hidden_icons():
                self.pressed = True
            elif now > 2.0:                      # the button never came: give everything back
                if win._class_name(win.user32.GetForegroundWindow()) == 'Shell_TrayWnd':
                    win.press(0x1B)
                self.finish()
            return
        panel = panel_window()
        if panel:
            self.panel = panel
            if self.placed_at is None:
                self.placed_at = time.monotonic()
            self.place_panel(panel)              # Windows places it by its clock: over the button instead
            if time.monotonic() - self.placed_at > PLACING / 3:
                set_alpha(panel, 255)            # in place: shown
            if time.monotonic() - self.placed_at > PLACING:
                self.ticker.setInterval(100)
            self.seen_open = True
        elif self.seen_open or now > 3.0:        # the panel closed (or never opened)
            self.finish()

    def finish(self) -> None:
        self.ticker.stop()
        if self.panel:
            set_alpha(self.panel, 255)           # never leave the panel see-through
        if not self.taskbar_up:
            self.dock.taskbar.hold(0.0)
            if self.dock.cfg['hide_taskbar']:
                win.show_taskbars(False)
            clear_taskbars(False)
        self.dock.veil = None
        self.deleteLater()
