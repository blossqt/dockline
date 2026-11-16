"""Live window previews above an app, as the Windows taskbar shows them on hover: Windows itself
(DWM) draws each window's live picture into the panel; click one to switch to it, or its close
button to close it."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
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

BOX_W, BOX_H, TITLE_H, PAD, GAP = 216, 150, 28, 10, 8
MOST = 6


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
        rise = round(dock.tile_height * 0.95) + 10          # clear of the magnified icon
        x = max(screen.left() + 8, min(centre - width // 2, screen.right() - width - 8))
        top = dock.mapToGlobal(QPoint(0, 0)).y()
        y = top + dock.height() + rise if dock.edge == 'top' else top - rise - height
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

    def close_now(self) -> None:
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
        self.update()
        self.leave_soon()

    def mouseMoveEvent(self, event):
        index = self.index_at(event.position().toPoint())
        if index != self.hover:
            self.hover = index
            self.update()

    def mouseReleaseEvent(self, event):
        pos = event.position()
        index = self.index_at(pos.toPoint())
        if index < 0:
            return
        window = self.windows[index]
        if event.button() == Qt.MiddleButton or self.close_box(index).contains(pos):
            win.close(window.hwnd)                           # as the taskbar's preview does
            QTimer.singleShot(250, self.strip.refresh)
            QTimer.singleShot(300, lambda: self.show_for(self.index) if self.isVisible() else None)
            return
        if event.button() == Qt.LeftButton:
            self.close_now()
            win.activate(window.hwnd)
            QTimer.singleShot(150, self.strip.refresh)
