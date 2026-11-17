"""Live window previews above an app, as the Windows taskbar shows them on hover (or on a click on
an app with several windows): Windows itself (DWM) draws each window's live picture into the panel.
Resting on one shows that window on the screen, the rest dimmed, with the dock and the previews still
over it, as the taskbar's peek does; click it to switch to it, or its close button to close it.

Windows' own preview pop-up can't be used: it belongs to the taskbar, which Dockline hides. Nor can
Windows' own peek (DwmpActivateLivePreview): it draws the peeked window over every other one - the
dock and the previews included (seen on screen, 2026-11-17); only the taskbar itself is let through.
So the peek is drawn here (PeekLayer): a layer over the screen, under the dock and the previews, with
the screen dimmed and the window's live picture where the window is."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from . import look as L
from . import win

dwmapi = ctypes.WinDLL('dwmapi')


class _ThumbProps(ctypes.Structure):
    _fields_ = [('dwFlags', wintypes.DWORD), ('rcDestination', wintypes.RECT), ('rcSource', wintypes.RECT),
                ('opacity', ctypes.c_ubyte), ('fVisible', wintypes.BOOL), ('fSourceClientAreaOnly', wintypes.BOOL)]


dwmapi.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)]
dwmapi.DwmUnregisterThumbnail.argtypes = [ctypes.c_void_p]
dwmapi.DwmUpdateThumbnailProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ThumbProps)]
dwmapi.DwmQueryThumbnailSourceSize.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.SIZE)]
RECT_DESTINATION, OPACITY, VISIBLE, CLIENT_ONLY = 0x1, 0x4, 0x8, 0x10


class _Placement(ctypes.Structure):
    _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT), ('showCmd', wintypes.UINT),
                ('ptMinPosition', wintypes.POINT), ('ptMaxPosition', wintypes.POINT),
                ('rcNormalPosition', wintypes.RECT)]


win.user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(_Placement)]
win.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
HWND_TOPMOST = wintypes.HWND(-1)
SWP_KEEP = 0x0001 | 0x0002 | 0x0010            # no size, no move, no activation


def _shown_rect(hwnd: int) -> wintypes.RECT:
    """Where a window is on screen, in physical pixels - where it would be, if minimised."""
    rect = wintypes.RECT()
    if win.user32.IsIconic(hwnd):
        placement = _Placement()
        placement.length = ctypes.sizeof(_Placement)
        if win.user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            rect = placement.rcNormalPosition
            if placement.flags & 0x2:                  # WPF_RESTORETOMAXIMIZED: its monitor's work area
                info = win._MonitorInfo()
                info.cbSize = ctypes.sizeof(info)
                monitor = win.user32.MonitorFromWindow(hwnd, 2)
                if monitor and win.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    rect = info.rcWork
        return rect
    win.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect


class PeekLayer(QWidget):
    """The peek: over the screen the window is on, the screen as it was dimmed and softened, and the
    window's live picture (DWM) where the window is. Ordinary (not layered): DWM draws thumbnails only
    into such windows. Takes no clicks and never the keyboard."""

    DIM = 150                                       # the black laid over the rest, of 255

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.backdrop: QPixmap | None = None
        self.thumb = ctypes.c_void_p()
        self.target = 0

    def show_window(self, hwnd: int, keep: list) -> None:
        """Peek at hwnd; keep (the dock, the previews; lowest first) stay over it."""
        rect = _shown_rect(hwnd)
        middle = QPoint((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        screen = None
        for candidate in QGuiApplication.screens():         # the screen holding the window's middle
            g = candidate.geometry()
            ratio = candidate.devicePixelRatio()
            physical = QRect(round(g.x() * ratio), round(g.y() * ratio), round(g.width() * ratio),
                             round(g.height() * ratio))
            if physical.contains(middle):
                screen = candidate
        screen = screen or QGuiApplication.primaryScreen()
        if not self.isVisible() or self.screen() is not screen:
            shot = screen.grabWindow(0)                      # before the layer is on screen: never in it
            image = shot.toImage()
            size = image.size()
            soft = image.scaled(max(1, size.width() // 6), max(1, size.height() // 6), Qt.IgnoreAspectRatio,
                                Qt.SmoothTransformation)
            self.backdrop = QPixmap.fromImage(soft.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            self.setGeometry(screen.geometry())
        self.drop_thumb()
        self.target = hwnd
        if not self.isVisible():
            self.show()
        own = wintypes.HWND(int(self.winId()))
        thumb = ctypes.c_void_p()
        if dwmapi.DwmRegisterThumbnail(own, wintypes.HWND(hwnd), ctypes.byref(thumb)) == 0:
            corner = wintypes.RECT()
            win.user32.GetWindowRect(own, ctypes.byref(corner))
            props = _ThumbProps()
            props.dwFlags = RECT_DESTINATION | OPACITY | VISIBLE | CLIENT_ONLY
            props.rcDestination = wintypes.RECT(rect.left - corner.left, rect.top - corner.top,
                                                rect.right - corner.left, rect.bottom - corner.top)
            props.opacity = 255
            props.fVisible = True
            props.fSourceClientAreaOnly = False
            dwmapi.DwmUpdateThumbnailProperties(thumb, ctypes.byref(props))
            self.thumb = thumb
        for kept in keep:                                    # the dock, then the previews, back on top
            if kept:
                win.user32.SetWindowPos(wintypes.HWND(kept), HWND_TOPMOST, 0, 0, 0, 0, SWP_KEEP)
        self.update()

    def drop_thumb(self) -> None:
        if self.thumb:
            dwmapi.DwmUnregisterThumbnail(self.thumb)
        self.thumb = ctypes.c_void_p()

    def end(self) -> None:
        self.drop_thumb()
        self.target = 0
        self.backdrop = None
        self.hide()

    def paintEvent(self, _):
        painter = QPainter(self)
        if self.backdrop is not None:
            painter.drawPixmap(QRectF(self.rect()), self.backdrop, QRectF(self.backdrop.rect()))
        painter.fillRect(self.rect(), QColor(0, 0, 0, self.DIM))


_layer = None


def peek(on: bool, hwnd: int = 0, keep=()) -> None:
    """Show one window on the screen with the rest dimmed and keep over it (on), or end that."""
    global _layer
    if not on:
        if _layer is not None:
            _layer.end()
        return
    if _layer is None:
        _layer = PeekLayer()
    _layer.show_window(hwnd, list(keep or []))


BOX_W, BOX_H, TITLE_H, PAD, GAP = 216, 150, 28, 10, 8
MOST = 6
ABOVE = 8               # between the dock and the panel, as the taskbar's (the icons shrink back meanwhile)
PEEK_AFTER = 400        # ms resting on a preview before its window is shown alone


class Previews(QWidget):
    """The panel of one app's windows. Not see-through: Windows draws live window pictures only
    into an ordinary (not layered) window."""

    def __init__(self, strip):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.strip = strip
        self.index = -1
        self.windows: list[win.Window] = []
        self.thumbs: list[ctypes.c_void_p] = []
        self.hover = -1
        self.hide_soon = QTimer(self)
        self.hide_soon.setSingleShot(True)
        self.hide_soon.setInterval(350)
        self.hide_soon.timeout.connect(self.close_now)
        self.peeking = 0                          # the window shown alone now, if any
        self.peek_soon = QTimer(self)
        self.peek_soon.setSingleShot(True)
        self.peek_soon.setInterval(PEEK_AFTER)
        self.peek_soon.timeout.connect(self.start_peek)

    # -- showing
    def show_for(self, index: int) -> None:
        self.hide_soon.stop()
        if not 0 <= index < len(self.strip.groups):
            return
        windows = self.strip.groups[index].windows[:MOST]
        if not windows:
            self.close_now()
            return
        if index == self.index and self.isVisible() and [w.hwnd for w in windows] == [w.hwnd for w in self.windows]:
            return
        self.drop_thumbs()
        self.index = index
        self.windows = windows
        count = len(windows)
        width = PAD * 2 + count * BOX_W + (count - 1) * GAP
        height = PAD * 2 + BOX_H
        tile = self.strip.rects()[index] if index < len(self.strip.rects()) else QRectF(0, 0, 40, 40)
        centre = self.strip.mapToGlobal(QPoint(round(tile.center().x()), 0)).x()
        dock = self.strip.dock
        screen = dock.screen().geometry()
        x = max(screen.left() + 8, min(centre - width // 2, screen.right() - width - 8))
        top = dock.mapToGlobal(QPoint(0, 0)).y()
        y = top + dock.height() + ABOVE if dock.edge == 'top' else top - ABOVE - height
        self.setGeometry(x, y, width, height)
        self.show()
        win.round_corners(int(self.winId()), True)
        self.register_thumbs()
        self.update()

    def box(self, i: int) -> QRect:
        return QRect(PAD + i * (BOX_W + GAP), PAD, BOX_W, BOX_H)

    def register_thumbs(self) -> None:
        ratio = self.devicePixelRatioF()
        own = wintypes.HWND(int(self.winId()))
        for i, window in enumerate(self.windows):
            thumb = ctypes.c_void_p()
            if dwmapi.DwmRegisterThumbnail(own, wintypes.HWND(window.hwnd), ctypes.byref(thumb)) != 0:
                self.thumbs.append(ctypes.c_void_p())
                continue
            size = wintypes.SIZE()
            dwmapi.DwmQueryThumbnailSourceSize(thumb, ctypes.byref(size))
            area = self.box(i).adjusted(6, TITLE_H, -6, -6)
            if size.cx > 0 and size.cy > 0:                 # keep the window's shape, centred in the box
                scale = min(area.width() / size.cx, area.height() / size.cy)
                w, h = size.cx * scale, size.cy * scale
                area = QRect(round(area.center().x() - w / 2), round(area.center().y() - h / 2), round(w), round(h))
            props = _ThumbProps()
            props.dwFlags = RECT_DESTINATION | OPACITY | VISIBLE | CLIENT_ONLY
            props.rcDestination = wintypes.RECT(round(area.left() * ratio), round(area.top() * ratio),
                                                round((area.right() + 1) * ratio), round((area.bottom() + 1) * ratio))
            props.opacity = 255
            props.fVisible = True
            props.fSourceClientAreaOnly = False
            dwmapi.DwmUpdateThumbnailProperties(thumb, ctypes.byref(props))
            self.thumbs.append(thumb)

    def drop_thumbs(self) -> None:
        for thumb in self.thumbs:
            if thumb:
                dwmapi.DwmUnregisterThumbnail(thumb)
        self.thumbs = []

    def start_peek(self) -> None:
        if 0 <= self.hover < len(self.windows) and self.isVisible():
            self.peeking = self.windows[self.hover].hwnd
            peek(True, self.peeking, self.kept_over())

    def kept_over(self) -> list:
        """What stays over a peeked window, lowest first: every dock, the magnified icons, these previews."""
        dock = self.strip.dock
        found = [int(d.winId()) for d in [dock, *dock.others] if d.isVisible()]
        layer = getattr(self.strip, '_magnifier', None)
        if layer is not None and layer.isVisible():
            found.append(int(layer.winId()))
        found.append(int(self.winId()))
        return found

    def end_peek(self) -> None:
        self.peek_soon.stop()
        if self.peeking:
            self.peeking = 0
            peek(False)

    def close_now(self) -> None:
        self.end_peek()
        self.drop_thumbs()
        self.index = -1
        self.windows = []
        self.hide()

    def leave_soon(self) -> None:
        if self.isVisible():
            self.hide_soon.start()

    # -- drawing
    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        look = self.strip.look
        dark = look.dark
        painter.fillRect(self.rect(), QColor(32, 32, 38) if dark else QColor(246, 246, 248))
        text = QColor('#f4f4f5' if dark else '#18181b')
        halo = L.HALO
        L.HALO = None                                        # solid ground here: no halo needed
        try:
            for i, window in enumerate(self.windows):
                box = QRectF(self.box(i))
                if i == self.hover:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 16))
                    painter.drawRoundedRect(box, 8, 8)
                L.text(painter, QRectF(box.x() + 8, box.y() + 4, box.width() - 38, TITLE_H - 6), window.title,
                       text, 12.5, 600, Qt.AlignLeft | Qt.AlignVCenter)
                if i == self.hover:
                    L.glyph(painter, self.close_box(i), '', text, 12)
                if i < len(self.thumbs) and not self.thumbs[i]:
                    L.text(painter, box.adjusted(0, TITLE_H, 0, 0), 'No preview', QColor(140, 140, 150), 12)
        finally:
            L.HALO = halo
        painter.setPen(QPen(QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 30), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))

    def close_box(self, i: int) -> QRectF:
        box = self.box(i)
        return QRectF(box.right() - 30, box.top() + 3, 26, TITLE_H - 6)

    # -- the pointer
    def index_at(self, pos) -> int:
        for i in range(len(self.windows)):
            if self.box(i).contains(pos):
                return i
        return -1

    def enterEvent(self, _):
        self.hide_soon.stop()

    def leaveEvent(self, _):
        self.hover = -1
        self.end_peek()
        self.update()
        self.leave_soon()

    def mouseMoveEvent(self, event):
        index = self.index_at(event.position().toPoint())
        if index != self.hover:
            self.hover = index
            self.update()
            if self.peeking:                               # already peeking: the next window at once
                self.peeking = 0
                if index >= 0:
                    self.start_peek()
                else:
                    peek(False)
            else:
                self.peek_soon.stop()
                if index >= 0:
                    self.peek_soon.start()

    def mouseReleaseEvent(self, event):
        pos = event.position()
        index = self.index_at(pos.toPoint())
        if index < 0:
            return
        window = self.windows[index]
        if event.button() == Qt.MiddleButton or self.close_box(index).contains(pos):
            self.end_peek()
            win.close(window.hwnd)                           # as the taskbar's preview does
            QTimer.singleShot(250, self.strip.refresh)
            QTimer.singleShot(300, lambda: self.show_for(self.index) if self.isVisible() else None)
            return
        if event.button() == Qt.LeftButton:
            self.close_now()
            win.activate(window.hwnd)
            QTimer.singleShot(150, self.strip.refresh)
