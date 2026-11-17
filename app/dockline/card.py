"""The panel a widget opens above (or below) itself on the dock."""

from __future__ import annotations

import time

import shiboken6
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QVBoxLayout, QWidget

from . import look as L
from . import motion
from . import suitetheme as theme
from . import win

RADIUS = 13
GLASS_RADIUS = 13       # the Glass style draws its own frosted pane now, with the same corners
PAD = 16
GROW_FROM = 0.9         # a card starts at this size, at its widget, and grows to full
RISE = 10              # and this many pixels nearer the dock


class Card(QWidget):
    closed_at = 0.0
    closed_for = None
    closing: list = []      # closed cards, kept from Python until Qt has deleted them (see closeEvent)

    def __init__(self, tile, content: QWidget, width: int = 300):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.tile = tile
        self.look = tile.dock.look
        self.backdrop = None                   # the Glass style: the screen behind, blurred (look.frosted_behind)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(L.card_css(self.look))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAD, PAD, PAD, PAD)
        layout.addWidget(content)
        self.content = content
        self.setFixedWidth(width)
        self.picture = None                    # the card as drawn, while it grows open
        self.progress = 1.0

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)     # what was drawn before goes:
        painter.fillRect(self.rect(), Qt.transparent)                  # glass drawn over it showed it through
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.picture is not None:
            eased = self.progress
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.setOpacity(min(1.0, eased * 1.6))
            factor = GROW_FROM + (1 - GROW_FROM) * eased
            painter.drawPixmap(motion.scaled(QRectF(self.rect()), self.origin(), factor, self.toward() * (1 - eased)),
                               self.picture, QRectF(self.picture.rect()))
            return
        if self.look.glass:                  # the dock's clear glass, over the screen behind blurred
            pane = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            L.paint_backdrop(painter, pane, GLASS_RADIUS, self.backdrop)
            L.paint_clear_glass(painter, pane, GLASS_RADIUS, self.tile.dock.panel_fill(), self.look.dark)
            return
        painter.setPen(QPen(self.look.c(self.look.edge), 1))
        painter.setBrush(self.look.c(self.look.panel))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)

    def open(self) -> None:
        self.adjustSize()
        dock = self.tile.dock
        tile_rect = self.tile.rect()
        top_left = self.tile.mapToGlobal(QPoint(0, 0))
        screen = (dock.screen() or QGuiApplication.primaryScreen()).geometry()
        x = top_left.x() + tile_rect.width() // 2 - self.width() // 2
        x = max(screen.left() + L.MARGIN, min(x, screen.right() - L.MARGIN - self.width()))
        dock_top = dock.mapToGlobal(QPoint(0, 0)).y()
        if dock.edge == 'top':
            y = dock_top + dock.height() + 8
        else:
            y = dock_top - self.height() - 8
        self.move(x, y)
        if self.look.glass:
            win.round_corners(int(self.winId()), False)  # its own corners, drawn round its frosted pane
        self.show()
        self.activateWindow()
        win.activate(int(self.winId()))
        if motion.on():
            self.grow()

    def refit(self) -> None:
        """Content inside an open card grew or shrank: resize and keep it 8 px clear of the dock."""
        if self.content.layout() is not None:
            self.content.layout().invalidate()
            self.content.layout().activate()
        self.content.adjustSize()
        self.layout().invalidate()
        self.layout().activate()
        self.resize(self.width(), self.layout().sizeHint().height())
        if not self.isVisible() or self.tile.dock.testing:
            return
        dock = self.tile.dock
        dock_top = dock.mapToGlobal(QPoint(0, 0)).y()
        y = dock_top + dock.height() + 8 if dock.edge == 'top' else dock_top - self.height() - 8
        self.move(self.x(), y)
        self.update()

    def origin(self) -> QPointF:
        """Where the card grows from and shrinks to: its widget's middle, on the dock's side."""
        centre = self.tile.mapToGlobal(QPoint(self.tile.width() // 2, 0)).x() - self.x()
        return QPointF(centre, 0 if self.tile.dock.edge == 'top' else self.height())

    def toward(self) -> float:
        return -RISE if self.tile.dock.edge == 'top' else RISE

    def grow(self) -> None:
        """Open as the original's cards do: from its widget, growing and fading in on its curve."""
        self.picture = self.grab()
        hidden = QGraphicsOpacityEffect(self.content)
        hidden.setOpacity(0.0)
        self.content.setGraphicsEffect(hidden)          # still there for the keyboard, just not drawn

        def step(progress):
            self.progress = progress
            self.update()

        def done():
            self.picture = None
            self.progress = 1.0
            try:
                self.content.setGraphicsEffect(None)
            except RuntimeError:
                return
            self.update()

        self.progress = 0.0
        motion.run(self, motion.OPEN_MS, step, done)

    def showEvent(self, event):
        # Qt sends this before the window is on screen: the screen behind is taken now, without it
        if self.look.glass and not self.tile.dock.testing and self.backdrop is None:
            self.backdrop = L.frosted_behind(self.geometry())
        super().showEvent(event)

    def closeEvent(self, event):
        if self.isVisible() and motion.on() and not self.tile.dock.testing:
            picture = self.grab() if self.picture is None else self.picture
            motion.Ghost(picture, self.geometry(), self.origin(), self.toward()).start()
        Card.closed_at = time.monotonic()
        Card.closed_for = self.tile
        # Deleted by Qt, from its event loop, and held here until then. With WA_DeleteOnClose and only
        # the tile's reference, Python freed the card itself the moment that reference went - which,
        # when a screenshot's overlay took the keyboard, was in the middle of Qt telling the card's
        # widgets their window was no longer active: memory freed twice, and Dockline crashed.
        Card.release_closed()
        Card.closing.append(self)
        QTimer.singleShot(0, self.deleteLater)
        self.tile.card_closed()
        super().closeEvent(event)

    @staticmethod
    def release_closed() -> None:
        """Let go of the closed cards Qt has deleted by now."""
        Card.closing = [card for card in Card.closing if shiboken6.isValid(card)]

    @staticmethod
    def just_closed(tile) -> bool:
        """The click that closed a tile's card should not open it again."""
        return Card.closed_for is tile and time.monotonic() - Card.closed_at < 0.35
