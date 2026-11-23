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

    # A window that says its glass is held (the bar while a card, menu or the previews are open over
    # it) keeps the picture it has: nothing behind it changed, and a new one, with that window just
    # above, read as a changed scene - the bar's own place was blended over from around it and its
    # tint thinned to the calm that made, so the bar went pale each time a card opened.
    capture = theme._capture_later

    def held_or_capture(window, final: bool) -> bool:
        held = getattr(window, 'glass_held', None)
        if held is not None and held():
            return True
        # While it slides (glass_sliding: the bar coming in or going out), no picture along the way -
        # each was blurred and gauged on the glass's thread, holding Python's lock from the slide's
        # frames (10-30 ms hitches) - but one once it has settled, as the shared glass takes anyway.
        sliding = getattr(window, 'glass_sliding', None)
        if not final and sliding is not None and sliding():
            return False
        return capture(window, final)

    theme._capture_later = held_or_capture

    # A window that draws its own corners (own_corners: the bar, 14 px round with its hairline) keeps
    # Windows' rounding off. The shared glass asks Windows for its round corners (8 px, with its own
    # border) on every frameless window as it shows: two shapes, one inside the other, at each of the
    # bar's ends. Nor Windows' blur behind, which fills the whole rectangle, corners and all.
    apply = theme._apply_glass

    def apply_keeping_corners(window) -> None:
        apply(window)
        if getattr(window, 'own_corners', False):
            hwnd = int(window.winId())
            theme._dwm(hwnd, theme._DWMWA_WINDOW_CORNER_PREFERENCE, 1)      # DWMWCP_DONOTROUND
            theme._accent(hwnd, theme._ACCENT_DISABLED)

    theme._apply_glass = apply_keeping_corners
