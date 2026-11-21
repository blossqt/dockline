"""The bar's glass (the suite's frosted glass, suitetheme.frost) blurs a live picture of the screen
around it. The shared glass paints the bar's own place out of each picture, and the images Windows
drags files with, but not Dockline's other windows: the magnified icons over the bar, the previews,
cards and menus above it. Caught in the picture, they were blurred into the bar like a reflection
of themselves (the user saw a dark smudge behind the icons). install() adds them to what each
picture paints out, as dragged images are (suitetheme._drag_images, healed from around each)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QRect

from . import suitetheme as theme

_user32 = ctypes.WinDLL('user32')                 # a copy of its own: the types set here touch no other code
_kernel32 = ctypes.WinDLL('kernel32')
_EACH = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumThreadWindows.argtypes = [wintypes.DWORD, _EACH, wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_window_thread = 0


def own_windows(native: QRect) -> list:
    """Where Dockline's own visible windows lie in a part of the screen (in Windows' own pixels),
    relative to it. Every window of Dockline's is its window thread's, so only that thread's are
    looked at (on the glass's own thread, a few microseconds)."""
    found = []

    def each(hwnd, _):
        box = wintypes.RECT()
        if _user32.IsWindowVisible(hwnd) and _user32.GetWindowRect(hwnd, ctypes.byref(box)):
            part = QRect(box.left, box.top, box.right - box.left, box.bottom - box.top).intersected(native)
            if not part.isEmpty():
                found.append(part.translated(-native.topLeft()))
        return True

    if _window_thread:
        _user32.EnumThreadWindows(_window_thread, _EACH(each), 0)
    return found


def install() -> None:
    """Call on the window thread, once theme.install() has run."""
    global _window_thread
    _window_thread = _kernel32.GetCurrentThreadId()
    original = theme._drag_images
    if getattr(original, 'with_own', False):
        return

    def with_own(native: QRect) -> list:
        return original(native) + own_windows(native)

    with_own.with_own = True
    theme._drag_images = with_own
