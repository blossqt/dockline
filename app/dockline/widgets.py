"""The dock's widgets: each a tile on the bar, most with a card that opens from it."""

from __future__ import annotations

import calendar
import ctypes
import datetime as dt
import os
import time
import urllib.parse
import webbrowser
import winreg
from typing import NamedTuple

from PySide6.QtCore import QEasingCurve, QFileInfo, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QIcon, QImage, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (QButtonGroup, QFileIconProvider, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from . import ai, audio, motion, shortcuts, sysinfo, weather, win
from . import look as L
from . import suitetheme as _theme
from .card import Card


def theme_frame() -> int:
    return _theme.frame_interval()


def _open(target: str) -> None:
    try:
        os.startfile(target)
    except OSError:
        pass


def _button(label: str, slot=None, name: str = '', checkable: bool = False) -> QPushButton:
    button = QPushButton(label)
    button.setCursor(Qt.PointingHandCursor)
    button.setFocusPolicy(Qt.NoFocus)
    if name:
        button.setObjectName(name)
    button.setCheckable(checkable)
    if slot:
        button.clicked.connect(slot)
    return button


def _label(value: str, name: str = '') -> QLabel:
    label = QLabel(value)
    if name:
        label.setObjectName(name)
    label.setWordWrap(True)
    return label


def _clock(seconds: float, tenths: bool = False) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(int(seconds), 3600)
    minutes, whole = divmod(rest, 60)
    text = f'{hours}:{minutes:02}:{whole:02}' if hours else f'{minutes:02}:{whole:02}'
    if tenths:
        text += f'.{int(seconds * 10) % 10}'
    return text


def small_only(icon: QIcon) -> bool:
    """Whether an icon has nothing bigger than about 64 pixels: the shell then hands back its small
    picture in the corner of an otherwise empty large one."""
    image = icon.pixmap(QSize(256, 256), 1.0).toImage()
    if image.isNull():
        return True
    image = image.convertToFormat(QImage.Format_Alpha8)
    width, height, line = image.width(), image.height(), image.bytesPerLine()
    if width < 128 or height < 128:
        return True
    corner = width // 4
    data = bytes(image.constBits())
    for y in range(height):
        row = data[y * line:y * line + width]
        if any(row if y >= corner else row[corner:]):
            return False
    return True


class Tile(QWidget):
    ID = ''
    NAME = ''
    GROUP = ''
    ABOUT = ''
    WIDTH = 1.0          # default width in units of the tile height
    MIN_WIDTH = 1.2      # narrowest a resizable widget can be dragged
    MAX_WIDTH = 10.0     # widest a resizable widget can be dragged
    PLAIN = False        # no tile background (the apps strip)
    STRETCH = False      # takes the dock's spare width

    def __init__(self, dock):
        super().__init__()
        self.dock = dock
        self.hover = False
        self.lit = 0.0           # the hover's light, 0 to 1, fading in and out
        self.pressed = 0.0       # how dark a press has made it, 0 to 1
        self._lighting = None
        self._pressing = None
        self.card: Card | None = None
        self._press: QPoint | None = None
        self._dragging = False
        self._resizing = False
        self._over_handle = False
        self._resize_from_x = 0
        self._resize_from_w = 0
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(dock.tile_height)
        if self.STRETCH:
            self.setMinimumWidth(dock.tile_height)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            self.setFixedWidth(round(dock.tile_height * self.current_units()))
        self.setToolTip(self.NAME)
        self.setup()

    def resizable(self) -> bool:
        return not self.PLAIN and not self.STRETCH and self.WIDTH > 1.0

    def min_units(self) -> float:
        return min(self.WIDTH, self.MIN_WIDTH)

    def current_units(self) -> float:
        if self.resizable():
            saved = (self.dock.cfg.get('widget_widths') or {}).get(self.ID)
            if isinstance(saved, (int, float)):
                return max(self.min_units(), min(self.MAX_WIDTH, float(saved)))
        return self.WIDTH

    def reset_width(self) -> None:
        widths = dict(self.dock.cfg.get('widget_widths') or {})
        if self.ID in widths:
            widths.pop(self.ID, None)
            self.dock.cfg['widget_widths'] = widths
            self.dock.cfg.save()
        self.setFixedWidth(round(self.dock.tile_height * self.WIDTH))
        self.update()
        self.dock.fit_now()

    # -- for widgets to fill in
    def setup(self): pass
    def tick(self): pass
    def paint(self, painter: QPainter, rect: QRectF, look: L.Look): pass
    def click(self, pos: QPoint): pass
    def menu(self, menu, pos: QPoint): pass
    def wheel(self, steps: float): pass
    def card_closed(self):
        self.card = None
        self.update()

    def retire(self):
        """The tile is leaving the dock: let go of anything outside it."""

    # -- motion
    def fade(self, attribute: str, to: float, ms: int) -> None:
        """Ease self.<attribute> to a value, redrawing as it goes."""
        holder = f'_{attribute}_anim'
        old = getattr(self, holder, None)
        if old is not None:
            try:
                old.stop()
            except RuntimeError:
                pass
        start = getattr(self, attribute)
        if abs(start - to) < 0.001:
            return

        def step(progress, start=start):
            setattr(self, attribute, start + (to - start) * progress)
            self.update()

        setattr(self, holder, motion.run(self, ms, step, easing=QEasingCurve(QEasingCurve.OutCubic)))

    def eased(self, key, value: float) -> float:
        """A reading as painted: gliding to each new one rather than jumping (motion.Glides)."""
        glides = getattr(self, '_glides', None)
        if glides is None:
            glides = self._glides = motion.Glides(self)
        return glides(key, value)

    def roll(self, key) -> motion.Roll:
        """Figures painted under key, rolling to each new value (motion.Roll)."""
        rolls = getattr(self, '_rolls', None)
        if rolls is None:
            rolls = self._rolls = {}
        if key not in rolls:
            rolls[key] = motion.Roll(self)
        return rolls[key]

    # -- helpers
    @property
    def look(self) -> L.Look:
        return self.dock.look

    def sizes(self) -> tuple[int, int]:
        return L.TEXT[self.dock.cfg['size']]

    def lines(self, painter, rect: QRectF, main: str, sub: str, align=Qt.AlignLeft, roll=None) -> None:
        """A reading and a line under it; with roll (a key), the reading's figures roll as it changes."""
        big, small = self.sizes()
        look = self.look
        if not sub:
            if roll is not None:
                self.roll(roll).paint(painter, rect, main, look.c(look.text), big, 600, align)
            else:
                L.text(painter, rect, main, look.c(look.text), big, 600, align | Qt.AlignVCenter)
            return
        height = big * 1.22 + small * 1.22
        top = rect.center().y() - height / 2
        main_rect = QRectF(rect.x(), top, rect.width(), big * 1.25)
        if roll is not None:
            self.roll(roll).paint(painter, main_rect, main, look.c(look.text), big, 600, align)
        else:
            L.text(painter, main_rect, main, look.c(look.text), big, 600, align | Qt.AlignVCenter)
        L.text(painter, QRectF(rect.x(), top + big * 1.22, rect.width(), small * 1.3), sub, look.c(look.sub),
               small, 400, align | Qt.AlignVCenter)

    def icon_and_lines(self, painter, rect: QRectF, icon: str, main: str, sub: str = '') -> None:
        side = rect.height()
        icon_rect = QRectF(rect.x() + 4, rect.y(), side * 0.62, side)
        L.glyph(painter, icon_rect, icon, self.look.c(self.look.text), side * 0.36)
        self.lines(painter, QRectF(icon_rect.right() + 2, rect.y(), rect.right() - icon_rect.right() - 8,
                                   side), main, sub)

    def ring_and_lines(self, painter, rect: QRectF, share: float, main: str, sub: str) -> None:
        """A gauge gliding to each reading, its figures rolling, and a line under them."""
        share = self.eased('ring', share)
        side = rect.height()
        ring_side = side * 0.62
        ring_rect = QRectF(rect.x() + (side - ring_side) / 2, rect.center().y() - ring_side / 2, ring_side,
                           ring_side)
        L.ring(painter, ring_rect, share, self.look.c(self.look.accent), self.look.c(self.look.track),
               max(3.0, side * 0.08))
        self.lines(painter, QRectF(rect.x() + side - 2, rect.y(), rect.width() - side - 4, side), main, sub,
                   roll='ring')

    def open_card(self, content: QWidget, width: int = 300) -> None:
        if self.dock.editing or Card.just_closed(self):
            return
        self.card = Card(self, content, width)
        if self.dock.testing:                    # drawn by --render and --self-test, never shown
            return
        self.card.open()
        self.update()

    def badge_rect(self) -> QRectF:
        return QRectF(2, 2, 15, 15)

    def handle_rect(self) -> QRectF:
        h = self.height()
        bar_h = max(14.0, h * 0.52)
        return QRectF(self.width() - 6.0, (h - bar_h) / 2.0, 4.0, bar_h)

    def handle_hit_rect(self) -> QRectF:
        return QRectF(max(18.0, self.width() - 11.0), 0.0, 11.0, float(self.height()))

    # -- events
    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        look = self.look
        rect = QRectF(self.rect())
        if not self.PLAIN:
            painter.setPen(Qt.NoPen)
            painter.setBrush(motion.mix(look.c(look.tile), look.c(look.hover), 1.0 if self.card else self.lit))
            painter.drawRoundedRect(rect, 10, 10)
        self.paint(painter, rect, look)
        if self.pressed > 0.01 and not self.PLAIN:       # the original's press: brightness down to 70%
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, round(255 * motion.PRESS_DARK * self.pressed)))
            painter.drawRoundedRect(rect, 10, 10)
        if self._dragging or self._resizing:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(L.chip(look), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 10, 10)
        if self.dock.editing:
            badge = self.badge_rect()
            painter.setPen(Qt.NoPen)
            painter.setBrush(L.chip(look))
            painter.drawEllipse(badge)
            painter.setPen(QPen(L.on_chip(look), 2, Qt.SolidLine, Qt.RoundCap))
            middle = badge.center().y()
            painter.drawLine(badge.left() + 4, middle, badge.right() - 4, middle)
            if self.resizable():
                grip = self.handle_rect()
                grip_color = QColor(L.chip(look))
                grip_color.setAlpha(240 if (self._over_handle or self._resizing) else 150)
                painter.setPen(Qt.NoPen)
                painter.setBrush(grip_color)
                painter.drawRoundedRect(grip, 2.0, 2.0)

    def enterEvent(self, _):
        self.hover = True
        self.fade('lit', 1.0, motion.HOVER_MS)

    def leaveEvent(self, _):
        self.hover = False
        if self._over_handle and not self._resizing:
            self._over_handle = False
            self.setCursor(Qt.PointingHandCursor)
        self.fade('lit', 0.0, motion.HOVER_MS)
        if self.pressed and self._press is None:
            self.fade('pressed', 0.0, motion.HOVER_MS)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position()
            if self.dock.editing and self.resizable() and self.handle_hit_rect().contains(pos):
                self._resizing = True
                self._resize_from_x = event.globalPosition().toPoint().x()
                self._resize_from_w = self.width()
                self._press = None
                self.update()
                return
            self._press = event.globalPosition().toPoint()
            self._dragging = False
            self.fade('pressed', 1.0, 0)                  # at once, as the original's does
        elif event.button() == Qt.RightButton:
            self.dock.context_menu(self, event.globalPosition().toPoint(), event.position().toPoint())

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.dock.editing and self.resizable()                 and self.handle_hit_rect().contains(event.position()):
            self._resizing = False
            self._press = None
            self.reset_width()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and event.buttons() & Qt.LeftButton:
            dx = event.globalPosition().toPoint().x() - self._resize_from_x
            lo = max(28, round(self.dock.tile_height * self.min_units()))
            hi = round(self.dock.tile_height * self.MAX_WIDTH)
            new_w = max(lo, min(hi, self._resize_from_w + dx))
            if new_w != self.width():
                self.setFixedWidth(new_w)
                self.update()
                self.dock.fit_now()
            return
        if self.dock.editing and self.resizable() and not (event.buttons() & Qt.LeftButton):
            over = self.handle_hit_rect().contains(event.position())
            if over != self._over_handle:
                self._over_handle = over
                self.setCursor(Qt.SizeHorCursor if over else Qt.PointingHandCursor)
                self.update()
        elif self._over_handle:
            self._over_handle = False
            self.setCursor(Qt.PointingHandCursor)
        if self._press is not None and event.buttons() & Qt.LeftButton:
            where = event.globalPosition().toPoint()
            if not self._dragging and (where - self._press).manhattanLength() > 8:
                self._dragging = True
                self.fade('pressed', 0.0, motion.HOVER_MS)
                self.dock.begin_reorder(self, self._press)
            if self._dragging:
                self.dock.reorder_to(self, where)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._resizing:
            self._resizing = False
            widths = dict(self.dock.cfg.get('widget_widths') or {})
            widths[self.ID] = round(self.width() / max(1, self.dock.tile_height), 3)
            self.dock.cfg['widget_widths'] = widths
            if not self.dock.testing:
                self.dock.cfg.save()
            self.dock.fit_now()
            self.update()
            return
        self.fade('pressed', 0.0, motion.HOVER_MS)
        if self._dragging:
            self._dragging = False
            self.dock.end_reorder(self)
            self.update()
        elif self._press is not None and self.rect().contains(event.position().toPoint()):
            pos = event.position().toPoint()
            if self.dock.editing:
                if self.badge_rect().adjusted(-4, -4, 4, 4).contains(pos):
                    self.dock.remove_widget(self.ID)
            else:
                self.click(pos)
        self._press = None

    def wheelEvent(self, event):
        self.wheel(event.angleDelta().y() / 120)


# ================================================================ Apps & Files

class StartTile(Tile):
    ID, NAME, GROUP = 'start', 'Start', 'Apps & Files'
    ABOUT = 'Opens the Start menu.'

    def paint(self, painter, rect, look):
        side = rect.height() * 0.38
        L.windows_logo(painter, QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side),
                       look.c(look.accent))

    def click(self, pos):
        win.open_start()


class Shown(NamedTuple):
    """One app as the strip shows it."""
    key: str
    group: win.Group
    index: int            # its place in AppsTile.groups, or -1 while it leaves
    presence: float       # 0 gone to 1 in its place, as it comes or goes


class AppsTile(Tile):
    ID, NAME, GROUP = 'apps', 'Apps', 'Apps & Files'
    ABOUT = 'Pinned apps and every open window. Click to switch, drag to rearrange, right-click to pin or close.'
    PLAIN = True

    GAP = 4                      # between apps, as the original's
    SETTLE = 0.35                # how far a moving icon goes toward its place each frame (of 60 a second)

    def setup(self):
        self.groups: list[win.Group] = []
        self.keys: list[str] = []
        self.order: list[str] = [key for key in self.dock.cfg['app_order']]
        self.icons: dict[str, QPixmap] = {}
        self.sized: dict[tuple[int, int], QPixmap] = {}           # icons made smaller ahead (sized_icon)
        self.blank_icons: dict[str, tuple[float, int]] = {}     # shown blank: when to look again, and how often so far
        self.hover_index = -1
        self.press_index = -1
        self.front = 0
        self.front_at_press = 0
        self.signature = None
        self.provider = QFileIconProvider()
        self.shown: dict[str, float] = {}         # where each icon is drawn while icons move about
        self.dragged: str | None = None           # the icon being dragged
        self.drag_x = 0.0
        self.grab_x = 0.0
        self.animator = QTimer(self)              # one clock for all that moves on the strip, run only then
        self.animator.timeout.connect(self.frame)
        self.leaving: dict[str, list] = {}        # apps whose last window closed: [group, the key it follows]
        self.presence: dict[str, float] = {}      # apps coming or going, 0 to 1 (all others are 1)
        self.changes: dict[str, tuple] = {}       # their moves: (start, from, to, ms)
        self.settling = False                     # icons sliding to their places around a dragged one
        self.launches: dict[str, dict] = {}       # apps started from the dock, until a window of theirs shows
        self.hops: dict[str, float] = {}          # when each started app's icon began to hop
        self.flash_at: dict[int, float] = {}      # when each window began asking for attention
        self.bars = motion.Glides(self, motion.HOVER_MS)     # the bar under the app in front widening
        self.torn = False                         # the dragged icon is away from the dock
        self.landing: str | None = None           # the dragged icon settling back in, still lifted
        self._lift = None
        self.held = QPointF()                     # where the dragged icon was taken, from its centre
        self.flashing: set[int] = set()           # windows asking for attention, as the taskbar shows them
        self.dwell = QTimer(self)                 # resting on an app: its windows' previews
        self.dwell.setSingleShot(True)
        self.dwell.setInterval(450)
        self.dwell.timeout.connect(self.show_previews)
        self._previews = None
        self.middle_index = -1
        self.soon = QTimer(self)                  # Windows said a window came, went or changed
        self.soon.setSingleShot(True)
        self.soon.setInterval(40)
        self.soon.timeout.connect(self.refresh)
        self.last_frame = time.perf_counter()
        self.dropping: list[str] = []             # files dragged over the apps
        self.drop_dwell = QTimer(self)            # a file held over an open app brings its window forward
        self.drop_dwell.setSingleShot(True)
        self.drop_dwell.setInterval(700)
        self.drop_dwell.timeout.connect(self.raise_for_drop)
        self.setAcceptDrops(True)
        # as wide as its apps (the dock is a compact bar that grows with them), narrower if it must
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.refresh()

    def weight(self) -> float:
        """How many apps the strip makes room for: those coming or going count as far as they are."""
        if not self.presence and not self.leaving:
            return float(len(self.groups))
        return sum(entry.presence for entry in self.display())

    def wanted(self, count: float, scale: float = 1.0) -> int:
        count = max(1.0, count)
        return round(count * self.dock.tile_height * scale + (count - 1) * self.GAP)

    def sizeHint(self):
        return QSize(self.wanted(self.weight()), self.dock.tile_height)

    def minimumSizeHint(self):
        return QSize(self.wanted(self.weight(), 0.6), self.dock.tile_height)

    @property
    def slot(self) -> float:
        """Each app's tile: square at the tile height, smaller when the dock is short of room."""
        count = max(1.0, self.weight())
        room = (self.width() - self.GAP * (count - 1)) / count
        if room >= self.dock.tile_height - 1:          # a pixel short as apps come or go: no wobble
            return float(self.dock.tile_height)
        return max(self.dock.tile_height * 0.6, room)

    def offset(self) -> float:
        return 0.0

    def magnifier(self):
        """The magnified apps' layer, made when first needed (never off screen)."""
        if self.dock.testing:
            return None
        if getattr(self, '_magnifier', None) is None:
            self._magnifier = motion.Magnifier(self)
        return self._magnifier

    def magnified(self) -> bool:
        layer = getattr(self, '_magnifier', None)
        return layer is not None and layer.isVisible()

    def magnifier_done(self):
        self.update()

    def moveEvent(self, event):                      # the strip moved in the row (a widget came or went)
        super().moveEvent(event)
        self.dock.keep_magnifier()

    def resizeEvent(self, event):                    # or grew for a new app
        super().resizeEvent(event)
        self.dock.keep_magnifier()

    def retire(self):
        self.animator.stop()
        layer = getattr(self, '_magnifier', None)
        if layer is not None:
            layer.close()
            layer.deleteLater()
            self._magnifier = None
        if self._lift is not None:
            self._lift.stop()
            self._lift.close()
            self._lift.deleteLater()
            self._lift = None
        if self._previews is not None:
            self._previews.close_now()
            self._previews.deleteLater()
            self._previews = None

    # -- the taskbar's ways: previews on hover, attention, new windows
    def previews(self):
        if self.dock.testing:
            return None
        if self._previews is None:
            from .previews import Previews
            self._previews = Previews(self)
        return self._previews

    def previews_visible(self) -> bool:
        return self._previews is not None and self._previews.isVisible()

    def show_previews(self, index: int | None = None) -> None:
        panel = self.previews()
        index = self.hover_index if index is None else index
        if panel is None or self.dragged is not None or self.card is not None or index < 0:
            return
        panel.show_for(index)
        self.follow_pointer(None)                          # the icons shrink back: the panel sits low

    def window_changed(self) -> None:
        """Windows told the dock a window opened, closed, came to the front or changed title."""
        if self.dragged is None:
            self.soon.start()

    def flash(self, hwnd: int, on: bool = True) -> bool:
        """A window requests attention (on), or has it again; True when it has just started to."""
        started = False
        if on and hwnd != win.foreground():
            if hwnd not in self.flashing:
                self.flash_at[hwnd] = time.perf_counter()
                started = True
            self.flashing.add(hwnd)
            self.kick()
        else:
            self.flashing.discard(hwnd)
            self.flash_at.pop(hwnd, None)
        self.update()
        return started

    def new_window(self, index: int) -> None:
        """Middle-click or Shift+click: another window of the app, as on the taskbar."""
        if 0 <= index < len(self.groups) and self.groups[index].path:
            _open(self.groups[index].path)
            self.launch(self.keys[index])

    # -- starting an app: its icon hops in its tile and its dot breathes until a window shows
    def launch(self, key: str) -> None:
        group = dict(zip(self.keys, self.groups)).get(key)
        if group is None:
            return
        now = time.perf_counter()
        self.launches[key] = {'at': now, 'count': len(group.windows),
                              'known': {w.hwnd for g in self.groups for w in g.windows}}
        if motion.on():
            self.hops[key] = now
        self.kick()
        self.update()

    def end_launches(self) -> None:
        """A started app is no longer shown as starting once a new window of its own shows, once a
        window that was not open before comes to the front (one started through a launcher, as
        Discord's, belongs to another program), or after LAUNCH_MOST seconds."""
        if not self.launches:
            return
        now = time.perf_counter()
        by_key = dict(zip(self.keys, self.groups))
        newest = max(self.launches, key=lambda key: self.launches[key]['at'])
        shown = {w.hwnd for g in self.groups for w in g.windows}
        for key, started in list(self.launches.items()):
            group = by_key.get(key)
            if group is None or now - started['at'] > motion.LAUNCH_MOST or len(group.windows) > started['count'] \
                    or (key == newest and self.front in shown and self.front not in started['known']):
                del self.launches[key]

    def rest_x(self, index: int) -> float:
        return index * (self.slot + self.GAP)

    def display(self) -> list[Shown]:
        """The apps as shown, in order: the open and pinned ones, and those leaving, where they were."""
        live = [Shown(key, group, i, self.presence.get(key, 1.0))
                for i, (key, group) in enumerate(zip(self.keys, self.groups))]
        if not self.leaving:
            return live
        after: dict = {}
        for key, (group, anchor) in self.leaving.items():
            after.setdefault(anchor, []).append(Shown(key, group, -1, self.presence.get(key, 0.0)))
        out: list[Shown] = []

        def emit(entry):
            out.append(entry)
            for follower in after.pop(entry.key, []):
                emit(follower)

        for follower in after.pop(None, []):
            emit(follower)
        for entry in live:
            emit(entry)
        for followers in list(after.values()):        # after an app no longer shown: at the end
            for follower in followers:
                emit(follower)
        return out

    def placed(self) -> list[tuple[Shown, QRectF]]:
        """Each app shown with its tile in this strip; while magnified, where the magnifier draws it.
        One coming or going takes that share of its room, drawn that much of its size."""
        if self.magnified():
            return self._magnifier.placed()
        slot = self.slot
        top = (self.height() - slot) / 2
        out, x = [], 0.0
        for entry in self.display():
            left = self.shown.get(entry.key, x)
            size = slot * entry.presence
            out.append((entry, QRectF(left, top + (slot - size) / 2, size, size)))
            x += (slot + self.GAP) * entry.presence
        return out

    def rects(self) -> list[QRectF]:
        """Each open or pinned app's tile where it is drawn, in this strip."""
        return [rect for entry, rect in self.placed() if entry.index >= 0]

    def targets(self) -> dict[str, float]:
        """Where each app's tile rests, from the strip's left."""
        out, x = {}, 0.0
        slot = self.slot
        for entry in self.display():
            out[entry.key] = x
            x += (slot + self.GAP) * entry.presence
        return out

    # -- apps coming and going: new ones grow in, closed ones shrink away, the others make room
    def can_move(self) -> bool:
        return motion.on() and self.isVisible() and self.dragged is None and not getattr(self.dock, 'tucked', False)

    def change(self, key: str, start: float, to: float, ms: int, now: float) -> None:
        self.presence[key] = start
        self.changes[key] = (now, start, to, ms)

    def move_apps(self, before: list[str], old_keys: list[str], old_groups: dict) -> None:
        """Apps came or went. New ones grow in where they belong and the others make room; closed
        ones shrink away where they were and the others close the gap. The dock grows and shrinks
        with them frame by frame (frame), so every icon keeps its size meanwhile: with the dock
        left to catch up, all of them shrank for a moment as one came."""
        now = time.perf_counter()
        live = set(self.keys)
        old = set(old_keys)
        for key in self.keys:
            if key not in old:
                self.leaving.pop(key, None)
                self.change(key, self.presence.get(key, 0.0), 1.0, motion.OPEN_MS, now)
        for i, key in enumerate(before):
            if key in live or key in self.leaving or key not in old_groups:
                continue
            anchor = next((k for k in reversed(before[:i]) if k in live or k in self.leaving), None)
            self.leaving[key] = [old_groups[key], anchor]
            self.change(key, self.presence.get(key, 1.0), 0.0, motion.CLOSE_MS, now)
        self.kick()

    def settle_apps(self) -> None:
        """Whatever is coming or going, at once where it ends (a drag begins, the dock is away)."""
        if not (self.changes or self.presence or self.leaving):
            return
        self.changes.clear()
        self.presence.clear()
        self.leaving.clear()
        self.updateGeometry()
        self.dock.fit_soon()

    def gone(self, key: str) -> None:
        _group, anchor = self.leaving.pop(key)
        self.presence.pop(key, None)
        for other in self.leaving.values():          # those that followed it follow what it followed
            if other[1] == key:
                other[1] = anchor

    def pulsing(self, now: float) -> bool:
        return any(now - at < motion.PULSE * motion.PULSES for at in self.flash_at.values())

    def kick(self) -> None:
        """The strip's clock: at the screen's rate while something moves, slower while something only
        pulses or breathes, stopped when nothing does."""
        now = time.perf_counter()
        hopping = any(now - at < motion.HOP_MS / 1000 for at in self.hops.values())
        if self.changes or self.settling or hopping:
            interval = max(4, theme_frame())
        elif motion.on() and (self.launches or self.pulsing(now)):
            interval = motion.PULSE_EVERY
        else:
            self.animator.stop()
            return
        if not self.animator.isActive():
            self.last_frame = now
            self.animator.start(interval)
        elif self.animator.interval() != interval:
            self.animator.setInterval(interval)

    def frame(self) -> None:
        now = time.perf_counter()
        frames = min(4.0, (now - self.last_frame) * 60)
        self.last_frame = now
        if self.changes:
            for key, (start, low, high, ms) in list(self.changes.items()):
                t = (now - start) * 1000 / ms
                if t < 1.0:
                    self.presence[key] = low + (high - low) * motion.ease(t)
                    continue
                del self.changes[key]
                if high >= 1.0:
                    self.presence.pop(key, None)
                elif key in self.leaving:
                    self.gone(key)
                else:
                    self.presence[key] = 0.0         # the dragged icon, away from the dock
            self.updateGeometry()
            self.dock.fit_now()
        if self.settling:
            self.settle(frames)
        self.hops = {key: at for key, at in self.hops.items() if now - at < motion.HOP_MS / 1000}
        self.end_launches()
        self.update()
        self.kick()

    def name_of(self, index: int) -> str:
        if not 0 <= index < len(self.groups):
            return ''
        group = self.groups[index]
        if len(group.windows) == 1:
            return group.windows[0].title
        return os.path.splitext(os.path.basename(group.path))[0] or 'App'

    def tick(self):
        if self.dragged is None:
            self.refresh()

    def refresh(self):
        windows = win.app_windows(os.getpid())
        windows = [w for w in windows if self.dock.shows_window(w.hwnd)]      # Windows' per-display setting
        groups: dict[str, win.Group] = {}
        program_pins: dict[str, str] = {}         # a program's windows show on the shortcut pinned for it
        for path in (self.dock.cfg['pinned'] if self.dock.shows_pinned() else []):
            groups[path.lower()] = win.Group(path, pinned=True)
            program = shortcuts.program_of(path)
            if program and program != path.lower() and not shortcuts.web_app(path):
                program_pins.setdefault(program, path.lower())
        for window in windows:
            key = (window.path or f'window:{window.hwnd}').lower()
            if key not in groups:
                key = program_pins.get(key, key)
            group = groups.get(key)
            if group is None:
                group = groups[key] = win.Group(window.path)
            group.windows.append(window)
        # the order the person arranged, then pinned apps, then newly opened ones at the end
        keys = [key for key in self.order if key in groups]
        keys += [key for key in groups if key not in keys]
        self.order = keys + [key for key in self.order if key not in groups]   # remembered while closed
        old_keys, old_groups = self.keys, dict(zip(self.keys, self.groups))
        before = [entry.key for entry in self.display()]
        self.keys = keys
        self.groups = [groups[key] for key in keys]
        self.front = win.foreground()
        alive = {w.hwnd for w in windows}
        self.flashing = {hwnd for hwnd in self.flashing if hwnd in alive and hwnd != self.front}
        self.flash_at = {hwnd: at for hwnd, at in self.flash_at.items() if hwnd in self.flashing}
        self.end_launches()
        signature = (tuple((key, len(groups[key].windows)) for key in keys), self.front, frozenset(self.flashing))
        if signature != self.signature:
            came_or_went = self.signature is not None and set(keys) != set(old_keys)
            count_changed = self.signature is None or len(self.signature[0]) != len(keys)
            self.signature = signature
            if came_or_went and self.can_move():
                self.move_apps(before, old_keys, old_groups)
            else:
                if came_or_went:
                    self.settle_apps()
                if count_changed:
                    self.updateGeometry()
                    self.dock.fit_soon()
            self.bars.forget(set(keys) | set(self.leaving))
            self.update()

    ICON = 0.62                  # an icon's share of its tile

    def icon_for(self, group: win.Group) -> QPixmap | None:
        """One sharp icon per app, at its largest magnified size; drawn smaller as needed."""
        key = group.path.lower() or f'window:{group.windows[0].hwnd if group.windows else 0}'
        if key in self.icons:
            retry = self.blank_icons.get(key)
            if retry is None or time.monotonic() < retry[0] or not group.windows:
                return self.icons[key]
            del self.icons[key]            # it showed the blank picture: its window may have its icon by now
        ratio = self.devicePixelRatioF()
        size = round(self.dock.tile_height * motion.MAGNIFY * self.ICON)
        pixmap = None
        app_id, packaged = win.app_id_of(group.windows[0]) if group.windows else ('', False)
        if packaged:                                     # a Store app: its package's logo, as the taskbar shows
            pixmap = self.app_id_pixmap(app_id, round(size * ratio))
        own = bool(group.path) and win.has_own_icon(group.path)
        if pixmap is None and own:                       # the shell's large icon is sharper than a window's
            icon = self.provider.icon(QFileInfo(group.path))
            found = icon.pixmap(QSize(size, size), ratio)
            if not found.isNull() and found.width() >= size * ratio * 0.5:
                pixmap = found
            if pixmap is not None and group.windows and small_only(icon):
                # A file with only a small icon (often the stock placeholder a build tool puts in) gives up
                # nothing in sharpness to an icon the window set itself, which is then the app's real one.
                handle = win.window_set_icon(group.windows[0].hwnd)
                image = QImage.fromHICON(handle) if handle else QImage()
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
        if pixmap is None and group.windows:
            handle = win.window_icon(group.windows[0].hwnd)
            if handle:
                image = QImage.fromHICON(handle)
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
        if pixmap is None and app_id and not packaged:   # a program whose icon is on its Start shortcut
            pixmap = self.app_id_pixmap(app_id, round(size * ratio))
        blank = False
        if pixmap is None and group.path:                # the shell's own, blank as it may be
            found = self.provider.icon(QFileInfo(group.path)).pixmap(QSize(size, size), ratio)
            if not found.isNull():
                pixmap, blank = found, True
        tries = self.blank_icons.pop(key, (0.0, 0))[1]
        if blank and group.windows and tries < 5:       # look again a little later, a few times
            self.blank_icons[key] = (time.monotonic() + 2.0 * (tries + 1), tries + 1)
        if pixmap is not None and (group.windows or group.pinned):
            self.icons[key] = pixmap
        return pixmap

    SIZED_KEPT = 160

    def sized_icon(self, pixmap: QPixmap, pixels: float) -> QPixmap:
        """The icon made smaller ahead, averaging its pixels, rather than drawn smaller: drawing samples
        only a few of them, which left the edges stepped (2026-11-27). Kept for the sizes asked lately."""
        wanted = round(pixels)
        if wanted <= 0 or wanted >= max(pixmap.width(), pixmap.height()):
            return pixmap
        key = (pixmap.cacheKey(), wanted)
        found = self.sized.get(key)
        if found is None:
            image = pixmap.toImage().scaled(wanted, wanted, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            found = QPixmap.fromImage(image)
            if len(self.sized) >= self.SIZED_KEPT:
                self.sized.pop(next(iter(self.sized)))
            self.sized[key] = found
        return found

    def app_id_pixmap(self, app_id: str, pixels: int) -> QPixmap | None:
        try:
            found = win.app_id_image(app_id, pixels)
        except OSError:
            found = None
        if found is None:
            return None
        data, width, height = found
        image = QImage(data, width, height, width * 4, QImage.Format_ARGB32_Premultiplied).copy()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        return pixmap

    def index_at(self, pos: QPoint) -> int:
        for index, rect in enumerate(self.rects()):
            if rect.left() - self.GAP / 2 <= pos.x() < rect.right() + self.GAP / 2:
                return index
        return -1

    def paint_app(self, painter: QPainter, index: int, rect: QRectF) -> None:
        self.paint_entry(painter, Shown(self.keys[index], self.groups[index], index, 1.0), rect)

    def paint_entry(self, painter: QPainter, entry: Shown, rect: QRectF, lifted: bool = False) -> None:
        """One app in its glass tile: hovered or in front lit, a bar under it while it has windows
        (wider and in the accent for the one in front). Coming or going, it fades with its size;
        started, its icon hops and its bar breathes; asking for attention, it pulses, then holds."""
        if rect.width() < 0.5:
            return
        look = self.look
        group, key, index = entry.group, entry.key, entry.index
        now = time.perf_counter()
        painter.save()
        if not lifted:
            painter.setOpacity(painter.opacity() * max(0.0, min(1.0, entry.presence)))
        focused = any(w.hwnd == self.front for w in group.windows)
        radius = rect.width() * 0.2
        fill = look.c(look.tile)
        if lifted:
            fill = look.c(look.hover)
        elif index >= 0 and index == self.hover_index:
            fill = motion.mix(fill, look.c(look.hover), self.lit)
        elif focused:
            fill = look.c(look.hover)
        asking = [self.flash_at.get(w.hwnd, now - 60) for w in group.windows if w.hwnd in self.flashing]
        if asking:                                     # as the taskbar shows it: a few pulses, then held
            level = motion.pulse(now - min(asking)) if motion.on() else 1.0
            fill = motion.mix(fill, QColor(255, 166, 64, 150), level)
        if lifted:
            painter.setPen(QPen(L.chip(look), 1))
        elif look.glass:
            painter.setPen(Qt.NoPen)                     # clear tiles: no outline, only a fill on hover
        else:
            painter.setPen(QPen(look.c(look.edge), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        size = rect.width() * self.ICON
        rise = 0.0
        if key in self.hops:                           # away from the screen's edge, inside its tile
            rise = motion.hop((now - self.hops[key]) * 1000 / motion.HOP_MS) * motion.HOP * rect.height()
            if self.dock.edge == 'top':
                rise = -rise
        target = QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2 - rect.height() * 0.03 - rise,
                        size, size)
        pixmap = self.icon_for(group)
        if pixmap is not None:
            ratio = painter.device().devicePixelRatioF() if painter.device() is not None else 1.0
            pixmap = self.sized_icon(pixmap, target.width() * ratio * painter.transform().m11())
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(look.c(look.track))
            painter.drawRoundedRect(target, size * 0.2, size * 0.2)
        started = self.launches.get(key)
        if group.windows or started is not None:
            front = self.bars(key, 1.0 if focused else 0.0)
            width = rect.width() * (0.1 + 0.2 * front)
            colour = motion.mix(look.c(look.sub), look.c(look.accent), front)
            if started is not None and motion.on():
                colour.setAlphaF(colour.alphaF() * motion.breath(now - started['at']))
            painter.setPen(Qt.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(QRectF(rect.center().x() - width / 2, rect.bottom() - 5, width, 2.5),
                                    1.25, 1.25)
        if not lifted and index >= 0 and index == self.press_index and self.pressed > 0.01 and self.dragged is None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, round(255 * motion.PRESS_DARK * self.pressed)))
            painter.drawRoundedRect(rect, radius, radius)
        painter.restore()

    def paint(self, painter, rect, look):
        if self.magnified():
            return                                       # the magnifier draws them, over the dock
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for entry, tile in self.placed():
            if entry.key in (self.dragged, self.landing):
                continue                                 # in the lifted icon's hands
            self.paint_entry(painter, entry, tile)

    # -- rearranging icons: the one dragged is lifted off and follows the pointer, the others slide
    #    aside; a pinned one pulled away from the dock comes off it, and let go there, is unpinned
    def lifter(self):
        if self._lift is None and not self.dock.testing:
            self._lift = motion.Lift(self)
        return self._lift

    def begin_icon_drag(self, pos: QPoint) -> None:
        index = self.press_index
        self.settle_apps()
        self.dragged = self.keys[index]
        self.torn = False
        self.landing = None                              # one still landing is put down where it is
        self.grab_x = pos.x() - self.rest_x(index)
        layer = getattr(self, '_magnifier', None)
        if layer is not None:
            layer.drop()                                 # no magnifying while icons are moved
        self.shown = {key: self.rest_x(i) for i, key in enumerate(self.keys)}
        centre = QPointF(self.rest_x(index) + self.slot / 2, self.height() / 2)
        self.held = QPointF(pos) - centre
        lift = self.lifter()
        if lift is not None:
            lift.lift(Shown(self.dragged, self.groups[index], index, 1.0), self.mapToGlobal(centre))
        self.update()

    def beyond(self, pos: QPoint) -> float:
        """How far the pointer is past the strip, away from the screen's edge."""
        return pos.y() - self.height() if self.dock.edge == 'top' else -pos.y()

    def drag_icon(self, pos: QPoint) -> None:
        slot = self.slot
        index = self.keys.index(self.dragged)
        group = self.groups[index]
        torn = group.pinned and bool(group.path) and self.beyond(pos) > self.dock.tile_height * motion.TEAR
        if torn != self.torn:                          # off the dock its room closes; back, it opens again
            self.torn = torn
            self.change(self.dragged, self.presence.get(self.dragged, 1.0 if torn else 0.0), 0.0 if torn else 1.0,
                        motion.CLOSE_MS if torn else motion.OPEN_MS, time.perf_counter())
        self.drag_x = max(0.0, min(self.width() - slot, pos.x() - self.grab_x))
        if not torn:
            centre = self.drag_x + slot / 2
            others = [key for key in self.keys if key != self.dragged]
            place = sum(1 for i in range(len(others)) if self.rest_x(i) + slot / 2 < centre)
            order = others[:place] + [self.dragged] + others[place:]
            if order != self.keys:
                by_key = dict(zip(self.keys, self.groups))
                self.keys = order
                self.groups = [by_key[key] for key in order]
        self.shown[self.dragged] = self.drag_x
        lift = self._lift
        if lift is not None and lift.isVisible():
            if group.pinned and group.path:              # free to leave the dock
                lift.carry(self.mapToGlobal(QPointF(pos)) - self.held, 'Unpin' if torn else '')
            else:                                        # along the row only
                lift.carry(self.mapToGlobal(QPointF(self.drag_x + slot / 2, self.height() / 2)))
        self.settling = True
        self.kick()
        self.update()

    def end_icon_drag(self) -> None:
        key = self.dragged
        index = self.keys.index(key)
        group = self.groups[index]
        torn = self.torn
        self.dragged = None
        self.torn = False
        rest = [k for k in self.order if k not in self.keys]
        self.order = self.keys + rest
        self.dock.cfg['app_order'] = self.order
        self.dock.cfg.save()
        lift = self._lift if self._lift is not None and self._lift.isVisible() else None
        if torn:
            self.shown.pop(key, None)
            if lift is not None:
                lift.vanish()
            self.pin(group.path, False)                  # gone, or left as an open app
        else:
            target = self.targets().get(key, self.rest_x(index))
            self.shown[key] = target
            if lift is not None:
                self.landing = key
                lift.land(self.mapToGlobal(QPointF(target + self.slot / 2, self.height() / 2)), self.landed)
        self.settling = True
        self.kick()
        self.update()

    def landed(self) -> None:
        self.landing = None
        self.update()

    def settle(self, frames: float) -> None:
        rate = 1 - (1 - self.SETTLE) ** frames if motion.on() else 1.0
        moving = False
        targets = self.targets()
        for key in list(self.shown):
            if key == self.dragged:
                continue
            target = targets.get(key)
            if target is None:
                self.shown.pop(key)
                continue
            here = self.shown[key]
            here += (target - here) * rate
            if abs(target - here) < 0.3:
                here = target
            else:
                moving = True
            self.shown[key] = here
        if not moving and self.dragged is None and self.landing is None:
            self.settling = False
            self.shown = {}

    def check_pointer(self, where: QPoint) -> None:
        """The dock's own look at the pointer (every 60 ms): magnified while it is not over the apps,
        the icons shrink back. Qt's word that it left can miss - a right-click menu holds the pointer -
        and the grown icons and their name then stayed on screen, the bar hidden and shown again under
        them."""
        layer = getattr(self, '_magnifier', None)
        if layer is None or not layer.want or self.dragged is not None or self.landing is not None:
            return
        if not QRect(self.mapToGlobal(QPoint(0, 0)), self.size()).adjusted(-2, -2, 2, 2).contains(where):
            self.follow_pointer(None)

    def follow_pointer(self, pos: QPoint | None) -> None:
        layer = self.magnifier()
        if layer is None:
            return
        if pos is None or self.dragged is not None or self.landing is not None or self.dock.editing \
                or self.card is not None or self.previews_visible():   # previews open: the icons rest, under them
            layer.follow(None)
        else:
            layer.follow(pos.x() / (self.slot + self.GAP))

    def mousePressEvent(self, event):
        self.dwell.stop()
        if self._previews is not None:
            self._previews.close_now()
        if event.button() == Qt.MiddleButton:
            self.middle_index = self.index_at(event.position().toPoint())
            return
        if event.button() == Qt.LeftButton:
            self.front_at_press = win.foreground()
            self.press_index = self.index_at(event.position().toPoint())
            self._press = event.globalPosition().toPoint()
            self.fade('pressed', 1.0, 0)
        elif event.button() == Qt.RightButton:
            self.dock.context_menu(self, event.globalPosition().toPoint(), event.position().toPoint())

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._press is not None and event.buttons() & Qt.LeftButton and self.press_index >= 0 \
                and not self.dock.editing:
            if self.dragged is None and (event.globalPosition().toPoint() - self._press).manhattanLength() > 8:
                self.fade('pressed', 0.0, motion.HOVER_MS)
                self.begin_icon_drag(self.mapFromGlobal(self._press))
            if self.dragged is not None:
                self.drag_icon(pos)
                return
        self.follow_pointer(pos)
        index = self.index_at(pos)
        if index != self.hover_index:
            self.hover_index = index
            self.lit = 0.0
            self.fade('lit', 1.0, motion.HOVER_MS)
            self.setToolTip('' if self.magnifier() is not None else self.name_of(index))
            if self.previews_visible():
                if index >= 0 and self.groups[index].windows:
                    self._previews.show_for(index)         # already showing: follow at once
                else:
                    self._previews.leave_soon()
            elif index >= 0 and self._press is None:
                self.dwell.start()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            index = self.index_at(event.position().toPoint())
            if index >= 0 and index == self.middle_index:
                self.new_window(index)
            self.middle_index = -1
            return
        if event.button() != Qt.LeftButton:
            return
        if self.dragged is None and self._press is not None and event.modifiers() & Qt.ShiftModifier:
            self.fade('pressed', 0.0, motion.HOVER_MS)
            self.new_window(self.index_at(event.position().toPoint()))
            self._press = None
            return
        self.fade('pressed', 0.0, motion.HOVER_MS)
        if self.dragged is not None:
            self.end_icon_drag()
        elif self._press is not None and self.rect().contains(event.position().toPoint()):
            pos = event.position().toPoint()
            if self.dock.editing:
                if self.badge_rect().adjusted(-4, -4, 4, 4).contains(pos):
                    self.dock.remove_widget(self.ID)
            else:
                self.click(pos)
        self._press = None

    def leaveEvent(self, event):
        self.hover_index = -1
        self.dwell.stop()
        if self._previews is not None:
            self._previews.leave_soon()
        self.follow_pointer(None)
        super().leaveEvent(event)

    def update(self):
        super().update()
        layer = getattr(self, '_magnifier', None)
        if layer is not None and layer.isVisible():
            layer.update()

    def click(self, pos):
        index = self.index_at(pos)
        if index < 0:
            return
        group = self.groups[index]
        if not group.windows:
            _open(group.path)
            self.launch(self.keys[index])
        elif len(group.windows) == 1:
            window = group.windows[0]
            if self.front_at_press == window.hwnd and not win.is_minimised(window.hwnd):
                win.minimise(window.hwnd)
            else:
                win.activate(window.hwnd)
        elif self.previews() is not None:                 # several: their previews, as on hover
            self.dwell.stop()
            self.show_previews(index)
        else:
            self.list_windows(group, index)
        QTimer.singleShot(150, self.refresh)

    def list_windows(self, group: win.Group, index: int):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(_label(os.path.splitext(os.path.basename(group.path))[0] or 'Windows', 'title'))
        layout.addSpacing(8)
        for window in group.windows:
            def pick(_=False, hwnd=window.hwnd):
                if self.card:
                    self.card.close()
                win.activate(hwnd)
            row = _button(window.title, pick, 'row')
            row.setToolTip(window.title)
            layout.addWidget(row)
        self.open_card(content, 320)

    def owns_menu(self, pos) -> bool:
        """A right-click on an app gives that app's menu alone, as the taskbar does; elsewhere on the
        strip, the dock's own menu shows."""
        return self.index_at(pos) >= 0

    def menu(self, menu, pos):
        index = self.index_at(pos)
        if index < 0:
            return
        group = self.groups[index]
        program = shortcuts.program_of(group.path) if group.path else ''
        if not program and group.windows and group.windows[0].path.lower().endswith('.exe'):
            program = group.windows[0].path
        if group.path:
            name = os.path.splitext(os.path.basename(group.path))[0] or 'App'
            key = self.keys[index]
            launch = menu.addAction(name, lambda: (_open(group.path), self.launch(key)))   # the taskbar's top line
            pixmap = self.icon_for(group)
            if pixmap is not None:
                launch.setIcon(QIcon(pixmap))
            if program:
                menu.addAction('Run as administrator', lambda: win.shell_open(program, verb='runas'))
                menu.addAction('Open file location', lambda: win.shell_open(
                    'explorer.exe', f'/select,{shortcuts.quote(program)}'))
            menu.addSeparator()
            if group.pinned:
                menu.addAction('Unpin from dock', lambda: self.pin(group.path, False))
            else:
                menu.addAction('Pin to dock', lambda: self.pin(group.path, True))
        if group.windows:
            menu.addSeparator()
            label = 'Close window' if len(group.windows) == 1 else 'Close all windows'
            menu.addAction(label, lambda: [win.close(w.hwnd) for w in group.windows])
            pids = {w.pid for w in group.windows if w.pid and not w.path.lower().endswith('\\explorer.exe')}
            if pids:
                menu.addAction('End task', lambda: [win.end_process(pid) for pid in pids])

    def pin(self, path: str, on: bool):
        pinned = [p for p in self.dock.cfg['pinned'] if p.lower() != path.lower()]
        if on:
            pinned.append(path)
        self.dock.cfg['pinned'] = pinned
        self.dock.cfg.save()
        self.refresh()

    # -- files dragged onto the apps: held over an open app, its window comes forward (as on the
    #    taskbar); dropped on an app, they open in it; programs and shortcuts dropped are pinned
    @staticmethod
    def dragged_files(mime) -> list[str]:
        if not mime.hasUrls():
            return []
        return [os.path.normpath(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]

    @staticmethod
    def all_programs(files: list[str]) -> bool:
        return bool(files) and all(f.lower().endswith(shortcuts.PROGRAMS) for f in files)

    def dragEnterEvent(self, event):
        files = self.dragged_files(event.mimeData())
        if not files or self.dock.editing:
            event.ignore()
            return
        self.dropping = files
        self.dock.dropping = True
        event.acceptProposedAction()
        self.drag_over(event.position().toPoint())

    def dragMoveEvent(self, event):
        if not self.dropping:
            event.ignore()
            return
        event.acceptProposedAction()
        self.drag_over(event.position().toPoint())

    def drag_over(self, pos: QPoint) -> None:
        index = self.index_at(pos)
        if index == self.hover_index:
            return
        self.hover_index = index
        self.lit = 0.0
        self.fade('lit', 1.0, motion.HOVER_MS)
        self.drop_dwell.stop()
        if index >= 0 and self.groups[index].windows and not self.all_programs(self.dropping):
            self.drop_dwell.start()

    def raise_for_drop(self) -> None:
        if self.dropping and 0 <= self.hover_index < len(self.groups) and self.groups[self.hover_index].windows:
            win.activate(self.groups[self.hover_index].windows[0].hwnd)

    def end_drop(self) -> None:
        self.dropping = []
        self.dock.dropping = False
        self.drop_dwell.stop()
        self.hover_index = -1
        self.fade('lit', 0.0, motion.HOVER_MS)

    def dragLeaveEvent(self, event):
        self.end_drop()

    def dropEvent(self, event):
        files = self.dropping or self.dragged_files(event.mimeData())
        index = self.index_at(event.position().toPoint())
        self.end_drop()
        if not files:
            event.ignore()
            return
        if self.all_programs(files):
            for path in files:
                if not any(p.lower() == path.lower() for p in self.dock.cfg['pinned']):
                    self.pin(shortcuts.keep(path), True)
            event.acceptProposedAction()
            return
        if 0 <= index < len(self.groups) and self.open_with(self.groups[index], files):
            event.acceptProposedAction()
        else:
            event.ignore()

    def open_with(self, group: win.Group, files: list[str]) -> bool:
        """Open files in an app: its pinned shortcut's program with the shortcut's own arguments, or
        the program its windows belong to."""
        shortcut = shortcuts.read(group.path) if group.path else shortcuts.Shortcut('')
        program = shortcuts.program_of(group.path) if group.path else ''
        arguments = shortcut.arguments if program and shortcut.target.lower() == program else ''
        if not program and group.windows and group.windows[0].path.lower().endswith('.exe'):
            program = group.windows[0].path
        if not program:
            return False
        line = ' '.join(part for part in [arguments] + [shortcuts.quote(f) for f in files] if part)
        return win.shell_open(program, line, shortcut.working_dir or None)


class SearchTile(Tile):
    ID, NAME, GROUP = 'search', 'Search', 'Apps & Files'
    ABOUT = 'Opens Windows search.'

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, 'search', look.c(look.text), rect.height() * 0.36)

    def click(self, pos):
        if not self.dock.testing:
            win.windows_search()                       # Windows' own search, as the taskbar's button opens


class RecycleTile(Tile):
    ID, NAME, GROUP = 'recycle', 'Recycle Bin', 'Apps & Files'
    ABOUT = 'Click to empty the Recycle Bin.'

    def setup(self):
        self.items, self.bytes = 0, 0
        self.count = 0
        self.read()

    def read(self):
        self.items, self.bytes = sysinfo.recycle_bin()
        self.update()

    def tick(self):
        self.count += 1
        if self.count % 10 == 0:
            self.read()

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, 'bin', look.c(look.text), rect.height() * 0.36)
        if self.items:
            badge = QRectF(rect.right() - 18, 6, 14, 14)
            painter.setPen(Qt.NoPen)
            painter.setBrush(L.chip(look))
            painter.drawEllipse(badge)
            L.text(painter, badge, str(min(self.items, 99)), L.on_chip(look), 9, 700)

    def click(self, pos):
        self.read()
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(_label('Recycle Bin', 'title'))
        words = (f'{self.items} item{"s" if self.items != 1 else ""} · {sysinfo.size_text(self.bytes)}'
                 if self.items else 'Empty')
        layout.addWidget(_label(words, 'sub'))
        layout.addSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(8)

        def empty():
            if self.card:
                self.card.close()
            sysinfo.empty_recycle_bin(int(self.dock.winId()))
            self.read()

        row.addWidget(_button('Open', lambda: (self.card and self.card.close(), _open('shell:RecycleBinFolder'))))
        empty_button = _button('Empty Recycle Bin', empty, 'primary')
        empty_button.setEnabled(bool(self.items))
        row.addWidget(empty_button)
        layout.addLayout(row)
        self.open_card(content, 300)


class StorageTile(Tile):
    ID, NAME, GROUP = 'storage', 'Storage', 'Apps & Files'
    ABOUT = 'Free space on drive C, with a usage bar.'
    WIDTH = 3.0

    def setup(self):
        self.count = 0
        self.free, self.total = sysinfo.storage()

    def tick(self):
        self.count += 1
        if self.count % 30 == 0:
            self.free, self.total = sysinfo.storage()
            self.update()

    def paint(self, painter, rect, look):
        big, small = self.sizes()
        inner = rect.adjusted(10, 0, -10, 0)
        L.text(painter, QRectF(inner.x(), inner.center().y() - big - 2, inner.width(), big * 1.3),
               f'C: {self.free:.0f} GB free', look.c(look.text), small + 1, 600, Qt.AlignLeft | Qt.AlignVCenter)
        used = 1 - self.free / self.total if self.total else 0
        L.bar(painter, QRectF(inner.x(), inner.center().y() + 5, inner.width(), 5), used, look.c(look.accent),
              look.c(look.track))

    def click(self, pos):
        _open('shell:MyComputerFolder')


class ScreenshotTile(Tile):
    ID, NAME, GROUP = 'screenshot', 'Screenshot Capture', 'Apps & Files'
    ABOUT = 'Capture the screen, a window or a region with Snipping Tool.'

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, 'camera', look.c(look.text), rect.height() * 0.36)

    def click(self, pos):
        _open('ms-screenclip:')


# ================================================================ System

class ProcessorTile(Tile):
    ID, NAME, GROUP = 'cpu', 'CPU', 'System'
    ABOUT = 'CPU load. Click to open Task Manager.'
    WIDTH = 1.9

    def setup(self):
        self.reader = sysinfo.Processor()
        self.reader.read()
        self.share = 0.0

    def tick(self):
        self.share = self.reader.read()
        self.update()

    def paint(self, painter, rect, look):
        self.ring_and_lines(painter, rect, self.share, f'{round(self.share * 100)}%', 'CPU')

    def click(self, pos):
        _open('taskmgr.exe')


class MemoryTile(Tile):
    ID, NAME, GROUP = 'ram', 'RAM', 'System'
    ABOUT = 'Memory in use. Click to open Task Manager.'
    WIDTH = 2.05

    def setup(self):
        self.share, self.used, self.total = sysinfo.memory()

    def tick(self):
        self.share, self.used, self.total = sysinfo.memory()
        self.update()

    def paint(self, painter, rect, look):
        self.ring_and_lines(painter, rect, self.share, f'{round(self.share * 100)}%', 'RAM')
        self.setToolTip(f'Memory: {self.used:.1f} of {self.total:.0f} GB in use')

    def click(self, pos):
        _open('taskmgr.exe')


class NetworkTile(Tile):
    ID, NAME, GROUP = 'network', 'Network', 'System'
    ABOUT = 'Download and upload speed.'
    WIDTH = 2.5

    def setup(self):
        self.reader = sysinfo.Network()
        self.reader.read()
        self.down = self.up = 0.0

    def tick(self):
        self.down, self.up = self.reader.read()
        self.update()

    def paint(self, painter, rect, look):
        big, small = self.sizes()
        row = small * 1.45
        top = rect.center().y() - row
        for i, (icon, value) in enumerate((('down', self.down), ('up', self.up))):
            line = QRectF(rect.x() + 8, top + i * row, rect.width() - 14, row)
            L.glyph(painter, QRectF(line.x(), line.y(), small + 4, row), icon, look.c(look.accent), small)
            L.text(painter, line.adjusted(small + 8, 0, 0, 0), sysinfo.rate(value), look.c(look.text), small + 0.5,
                   600, Qt.AlignLeft | Qt.AlignVCenter)

    def click(self, pos):
        _open('ms-settings:network')


class BatteryTile(Tile):
    ID, NAME, GROUP = 'battery', 'Battery', 'System'
    ABOUT = 'Battery charge and charging state.'
    WIDTH = 2.15

    def setup(self):
        self.state = sysinfo.battery()

    def tick(self):
        self.state = sysinfo.battery()
        self.update()

    def paint(self, painter, rect, look):
        side = rect.height()
        body = QRectF(rect.x() + 10, rect.center().y() - side * 0.15, side * 0.5, side * 0.3)
        painter.setPen(QPen(look.c(look.sub), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(body, 4, 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(look.c(look.sub))
        painter.drawRoundedRect(QRectF(body.right() + 1.5, body.center().y() - 3, 2.5, 6), 1, 1)
        text_rect = QRectF(body.right() + 9, rect.y(), rect.right() - body.right() - 12, side)
        if self.state is None:
            self.lines(painter, text_rect, 'AC', 'No battery')
            return
        percent, plugged = self.state
        colour = look.c(look.accent) if plugged or percent > 20 else QColor('#ff6b6b')
        fill = body.adjusted(2.5, 2.5, -2.5, -2.5)
        fill.setWidth(fill.width() * percent / 100)
        painter.setBrush(colour)
        painter.drawRoundedRect(fill, 2, 2)
        self.lines(painter, text_rect, f'{percent}%', 'Charging' if plugged else 'On battery')

    def click(self, pos):
        _open('ms-settings:batterysaver')


class QuickSettingsTile(Tile):
    ID, NAME, GROUP = 'quick', 'Quick Settings', 'System'
    ABOUT = "Wi-Fi, Bluetooth, airplane mode and the rest of Windows' quick settings."

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, 'toggles', look.c(look.text), rect.height() * 0.36)

    def click(self, pos):
        win.through_notification_center((win.VK_LWIN, ord('A')), QTimer.singleShot)


class NotificationsTile(Tile):
    ID, NAME, GROUP = 'notifications', 'Notifications', 'System'
    ABOUT = 'Opens the notification center and calendar.'

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, 'bell', look.c(look.text), rect.height() * 0.36)

    def click(self, pos):
        win.notification_center()


class TaskViewTile(Tile):
    ID, NAME, GROUP = 'taskview', 'Task View', 'Apps & Files'
    ABOUT = 'Open windows and virtual desktops.'

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, '\ue7c4', look.c(look.text), rect.height() * 0.36)

    def click(self, pos):
        win.through_notification_center((win.VK_LWIN, 0x09), QTimer.singleShot)


class TrayTile(Tile):
    ID, NAME, GROUP = 'tray', 'Tray', 'System'
    ABOUT = "Opens the notification area's hidden icons."

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, 'chevron_up', look.c(look.text), rect.height() * 0.34)

    def click(self, pos):
        self.dock.open_tray()


IME_MODES = {'zh': ('中', '英'), 'ja': ('あ', 'A'), 'ko': ('가', 'A')}     # the taskbar's own marks


class LanguageTile(Tile):
    ID, NAME, GROUP = 'language', 'Keyboard Language', 'System'
    ABOUT = 'The input language of the active window. Click to switch the language or input mode.'
    WIDTH = 1.3

    def setup(self):
        self.shown = ('', '')
        self.target = 0                           # the window the language is for (the dock never takes the keyboard)
        self.watch = QTimer(self)                 # Win+Space or Shift change it at any time
        self.watch.setInterval(350)
        self.watch.timeout.connect(self.tick)
        if not self.dock.testing:
            self.watch.start()

    def reading(self) -> tuple[str, str]:
        if self.dock.testing:
            return 'ENG', 'US'
        front = win.foreground()
        if front and win.window_pid(front) != os.getpid():
            self.target = front
        locale = win.locale_of(win.keyboard_of(self.target))
        language = locale.split('-')[0].lower()
        code = (win.locale_info(locale, win.LOCALE_SISO639LANGNAME2) or language or '?').upper()
        region = win.locale_info(locale, win.LOCALE_SISO3166CTRYNAME)
        if language in IME_MODES:
            native = win.ime_native(self.target)
            if native is not None:
                return IME_MODES[language][0 if native else 1], region
        return code, region

    def tick(self):
        if not self.isVisible() and not self.dock.testing:
            return
        shown = self.reading()
        if shown != self.shown:
            self.shown = shown
            self.update()

    def paint(self, painter, rect, look):
        if not self.shown[0]:
            self.shown = self.reading()
        main, sub = self.shown
        self.lines(painter, rect, main, sub, Qt.AlignHCenter)
        self.setToolTip('Input language')

    def click(self, pos):
        target = self.target if not self.dock.testing else 0
        current = win.keyboard_of(target) if target else 0
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(_label('Input language', 'title'))
        layout.addSpacing(8)

        def back_to_window():
            if self.card:
                self.card.close()
            if target:
                win.activate(target)

        def use(_=False, hkl=0):
            back_to_window()
            if target:
                win.switch_keyboard(target, hkl)
            QTimer.singleShot(150, self.tick)

        for hkl in (win.keyboards() if not self.dock.testing else []):
            name = win.locale_info(win.locale_of(hkl), win.LOCALE_SLOCALIZEDDISPLAYNAME) or hex(hkl)
            row = _button(name, lambda _=False, h=hkl: use(hkl=h), 'row', checkable=True)
            row.setChecked(hkl == current)
            layout.addWidget(row)
        language = win.locale_of(current).split('-')[0].lower() if current else ''
        native = win.ime_native(target) if target and language in IME_MODES else None
        if native is not None:
            layout.addSpacing(8)
            layout.addWidget(_label('Input mode', 'sub'))
            name = win.locale_info(win.locale_of(current), win.LOCALE_SLOCALIZEDDISPLAYNAME).split(' (')[0]

            def mode(_=False, on=True):
                back_to_window()
                win.set_ime_native(target, on)
                QTimer.singleShot(150, self.tick)

            for on, label in ((True, f'{IME_MODES[language][0]}  {name}'), (False, f'{IME_MODES[language][1]}  English')):
                row = _button(label, lambda _=False, o=on: mode(on=o), 'row', checkable=True)
                row.setChecked(native == on)
                layout.addWidget(row)
        layout.addSpacing(8)
        layout.addWidget(_button('Language settings', lambda: (self.card and self.card.close(),
                                                               _open('ms-settings:regionlanguage')), 'row'))
        self.open_card(content, 300)


class ShowDesktopTile(Tile):
    ID, NAME, GROUP = 'desktop', 'Show Desktop', 'System'
    ABOUT = 'Minimises every window, or restores them.'

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, 'desktop', look.c(look.text), rect.height() * 0.36)

    def click(self, pos):
        win.show_desktop()


class KeyboardTile(Tile):
    ID, NAME, GROUP = 'keyboard', 'On-Screen Keyboard', 'System'
    ABOUT = "Opens Windows' On-Screen Keyboard, or closes it."

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, 'keyboard', look.c(look.text), rect.height() * 0.38)

    def click(self, pos):
        win.toggle_on_screen_keyboard()          # it types into the window in front (the dock never takes the keyboard)


THEME_KEY = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'


def windows_light() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_KEY) as key:
            return bool(winreg.QueryValueEx(key, 'AppsUseLightTheme')[0])
    except OSError:
        return True


def set_windows_light(light: bool) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_KEY, 0, winreg.KEY_SET_VALUE) as key:
            for name in ('AppsUseLightTheme', 'SystemUsesLightTheme'):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(light))
    except OSError:
        return
    words = ctypes.c_wchar_p('ImmersiveColorSet')
    result = ctypes.c_size_t()
    win.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, ctypes.cast(words, ctypes.c_void_p).value or 0, 0x2, 200,
                                   ctypes.byref(result))


class ThemeTile(Tile):
    ID, NAME, GROUP = 'theme', 'Theme Toggle', 'System'
    ABOUT = 'Switches Windows between light and dark mode.'

    def setup(self):
        self.light = windows_light()

    def tick(self):
        light = windows_light()
        if light != self.light:
            self.light = light
            self.update()

    def paint(self, painter, rect, look):
        L.glyph(painter, rect, 'sun' if self.light else 'moon', look.c(look.text), rect.height() * 0.36)
        self.setToolTip('Switch to dark mode' if self.light else 'Switch to light mode')

    def click(self, pos):
        self.light = not self.light
        set_windows_light(self.light)
        self.update()


class DayProgressTile(Tile):
    ID, NAME, GROUP = 'day', 'Day Progress', 'System'
    ABOUT = 'How much of the day has passed.'
    WIDTH = 1.9

    def share(self) -> float:
        now = dt.datetime.now()
        return (now.hour * 3600 + now.minute * 60 + now.second) / 86400

    def tick(self):
        self.update()

    def paint(self, painter, rect, look):
        share = self.share()
        self.ring_and_lines(painter, rect, share, f'{int(share * 100)}%', 'Day')


# ================================================================ Media & Audio

MUSIC = ''                 # Fluent's music note


class _MediaButton(QWidget):
    """Previous, play or pause, or next on a media page: a glyph, a circle under the pointer."""

    def __init__(self, page, kind: str, big: bool = False):
        super().__init__(page)
        self.page, self.kind, self.big = page, kind, big
        self.hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(44 if big else 36, 44 if big else 36)

    def enterEvent(self, _):
        self.hover = True
        self.update()

    def leaveEvent(self, _):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()) and self.isEnabled():
            self.page.pressed(self.kind)

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        ink = self.page.ink()
        if not self.isEnabled():
            ink.setAlpha(90)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.big:
            painter.setPen(Qt.NoPen)
            painter.setBrush(ink)
            painter.drawEllipse(rect)
            ink = self.page.under_ink()
        elif self.hover:
            fill = QColor(ink)
            fill.setAlpha(40)
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawEllipse(rect)
        if self.kind != 'toggle':
            L.glyph(painter, rect, 'prev' if self.kind == 'previous' else 'next', ink, rect.height() * 0.42)
            return
        painter.setPen(Qt.NoPen)                          # play and pause drawn as shapes: crisp at any size
        painter.setBrush(ink)
        side = rect.height() * 0.34
        middle = rect.center()
        if self.page.session.playing:
            bar = side * 0.32
            for x in (middle.x() - side / 2, middle.x() + side / 2 - bar):
                painter.drawRoundedRect(QRectF(x, middle.y() - side / 2, bar, side), bar / 3, bar / 3)
        else:
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QPolygonF
            left = middle.x() - side * 0.36
            painter.drawPolygon(QPolygonF([QPointF(left, middle.y() - side / 2), QPointF(left, middle.y() + side / 2),
                                           QPointF(left + side * 0.9, middle.y())]))


def _minutes(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return f'{hours}:{rest // 60:02}:{rest % 60:02}' if hours else f'{rest // 60}:{rest % 60:02}'


class _MediaProgress(QWidget):
    """How far into the track or video, with the time gone and its length; dragged or clicked to
    jump there, where the app allows it."""

    HEIGHT = 30

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.dragging: float | None = None             # where it is being dragged to (0..1)
        self.hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)

    def share(self) -> float:
        session = self.page.session
        if self.dragging is not None:
            return self.dragging
        if not session.duration:
            return 0.0
        return max(0.0, min(1.0, session.now() / session.duration))

    def track(self) -> QRectF:
        return QRectF(4, 6, self.width() - 8, 4)

    def share_at(self, x: float) -> float:
        line = self.track()
        return max(0.0, min(1.0, (x - line.left()) / max(1.0, line.width())))

    def seekable(self) -> bool:
        return self.page.session.can_seek

    def enterEvent(self, _):
        self.hover = True
        self.setCursor(Qt.PointingHandCursor if self.seekable() else Qt.ArrowCursor)
        self.update()

    def leaveEvent(self, _):
        self.hover = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.seekable():
            self.dragging = self.share_at(event.position().x())
            self.update()

    def mouseMoveEvent(self, event):
        if self.dragging is not None:
            self.dragging = self.share_at(event.position().x())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.dragging is None:
            return
        share, self.dragging = self.dragging, None
        self.page.seek(share * self.page.session.duration)
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        session = self.page.session
        ink = self.page.ink()
        track = QColor(ink)
        track.setAlpha(60)
        line = self.track()
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(line, 2, 2)
        share = self.share()
        painter.setBrush(ink)
        painter.drawRoundedRect(QRectF(line.left(), line.top(), line.width() * share, line.height()), 2, 2)
        if self.seekable() and (self.hover or self.dragging is not None):
            painter.drawEllipse(QPointF(line.left() + line.width() * share, line.center().y()), 6, 6)
        if session.duration:
            soft = QColor(ink)
            soft.setAlpha(170)
            gone = share * session.duration
            L.text(painter, QRectF(line.left(), 13, 80, 16), _minutes(gone), soft, 11, 400, Qt.AlignLeft | Qt.AlignVCenter)
            L.text(painter, QRectF(line.right() - 80, 13, 80, 16), _minutes(session.duration), soft, 11, 400,
                   Qt.AlignRight | Qt.AlignVCenter)


class _MediaPage(QWidget):
    """One app's session, as a card in Android's media player: its art behind, its name, the title
    and artist, and the controls."""

    HEIGHT = 190

    def __init__(self, carousel, session):
        super().__init__(carousel)
        self.carousel = carousel
        self.session = None
        self.art = QPixmap()
        self.buttons = {kind: _MediaButton(self, kind, kind == 'toggle') for kind in ('previous', 'toggle', 'next')}
        self.progress = _MediaProgress(self)
        self.show_session(session)

    def show_session(self, session) -> None:
        if self.session is None or session.art != self.session.art:
            self.art = QPixmap()
            if session.art:
                self.art.loadFromData(session.art)
        self.session = session
        self.progress.setVisible(session.duration > 0)
        self.progress.update()
        self.buttons['previous'].setEnabled(session.can_previous)
        self.buttons['next'].setEnabled(session.can_next)
        self.buttons['toggle'].setEnabled(session.can_toggle)
        self.update()
        for button in self.buttons.values():
            button.update()

    def on_art(self) -> bool:
        return not self.art.isNull()

    def ink(self) -> QColor:
        return QColor('#ffffff') if self.on_art() else self.carousel.look.c(self.carousel.look.text)

    def under_ink(self) -> QColor:
        return QColor('#111114') if self.on_art() or self.carousel.look.dark else QColor('#fafafa')

    def pressed(self, kind: str) -> None:
        self.carousel.command(self.session, kind)

    def seek(self, seconds: float) -> None:
        self.carousel.seek(self.session, seconds)

    def resizeEvent(self, _):
        self.progress.setGeometry(10, 80, self.width() - 20, _MediaProgress.HEIGHT)
        middle, bottom = self.width() / 2, self.height() - 14
        toggle = self.buttons['toggle']
        toggle.move(round(middle - toggle.width() / 2), round(bottom - toggle.height()))
        for kind, shift in (('previous', -60), ('next', 60)):
            button = self.buttons[kind]
            button.move(round(middle + shift - button.width() / 2), round(bottom - toggle.height() / 2 - button.height() / 2))

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        look = self.carousel.look
        rect = QRectF(self.rect())
        from PySide6.QtGui import QPainterPath, QLinearGradient
        shape = QPainterPath()
        shape.addRoundedRect(rect, 12, 12)
        painter.setClipPath(shape)
        if self.on_art():                                  # the art fills the card, darkened to read on
            scaled = self.art.size().scaled(self.size(), Qt.KeepAspectRatioByExpanding)
            source = QRectF((scaled.width() - self.width()) / 2, (scaled.height() - self.height()) / 2,
                            self.width(), self.height())
            ratio = self.art.width() / max(1, scaled.width())
            painter.drawPixmap(rect, self.art, QRectF(source.x() * ratio, source.y() * ratio,
                                                      source.width() * ratio, source.height() * ratio))
            shade = QLinearGradient(0, 0, 0, rect.height())
            shade.setColorAt(0, QColor(0, 0, 0, 120))
            shade.setColorAt(1, QColor(0, 0, 0, 190))
            painter.fillRect(rect, shade)
        else:
            painter.fillRect(rect, look.c(look.hover))
        ink = self.ink()
        soft = QColor(ink)
        soft.setAlpha(190)
        session = self.session
        L.text(painter, QRectF(14, 10, rect.width() - 28, 18), session.app, soft, 12, 500, Qt.AlignLeft | Qt.AlignVCenter)
        L.text(painter, QRectF(14, 34, rect.width() - 28, 22), session.title or 'Unknown title', ink, 16, 600,
               Qt.AlignLeft | Qt.AlignVCenter)
        L.text(painter, QRectF(14, 56, rect.width() - 28, 18), session.artist, soft, 13, 400,
               Qt.AlignLeft | Qt.AlignVCenter)


class _MediaCarousel(QWidget):
    """Every app's session side by side, one in view: the wheel turns them, the dots show which."""

    DOTS = 18

    def __init__(self, tile, sessions, parent=None):
        super().__init__(parent)                          # made inside the card: never a window of its own
        self.tile = tile
        self.look = tile.look
        self.pages: list[_MediaPage] = []
        self.index = 0
        self.shown = 0.0                                   # the page in view, fractional while sliding
        self._slide = None
        self._wheel = 0.0
        self.setFixedHeight(_MediaPage.HEIGHT)
        self.clock = QTimer(self)                          # the progress bars move while something plays
        self.clock.setInterval(250)
        self.clock.timeout.connect(self.tick_progress)
        self.clock.start()
        self.show_sessions(sessions)

    def tick_progress(self) -> None:
        for page in self.pages:
            if page.session.playing and page.session.duration:
                page.progress.update()

    def show_sessions(self, sessions) -> None:
        current = self.pages[self.index].session.id if self.pages else ''
        if [p.session.id for p in self.pages] == [s.id for s in sessions]:
            for page, session in zip(self.pages, sessions):
                page.show_session(session)
            return
        for page in self.pages:
            page.deleteLater()
        self.pages = [_MediaPage(self, session) for session in sessions]
        ids = [s.id for s in sessions]
        self.index = ids.index(current) if current in ids else 0
        self.shown = float(self.index)
        for page in self.pages:
            page.show()
        # the dots' strip only with pages to show dots for: with one, the card's foot would be
        # twice as deep as its head
        height = _MediaPage.HEIGHT + (self.DOTS if len(self.pages) > 1 else 0)
        if height != self.minimumHeight():
            self.setFixedHeight(height)
            card = getattr(self.tile, 'card', None)
            if card is not None and card.isVisible():
                card.refit()
        self.lay_out()
        self.update()

    def lay_out(self) -> None:
        for number, page in enumerate(self.pages):
            page.setGeometry(round((number - self.shown) * (self.width() + 12)), 0, self.width(), _MediaPage.HEIGHT)

    def resizeEvent(self, _):
        self.lay_out()

    def go(self, index: int) -> None:
        index = max(0, min(len(self.pages) - 1, index))
        if index == self.index:
            return
        self.index = index
        start = self.shown
        if self._slide is not None:
            try:
                self._slide.stop()
            except RuntimeError:
                pass

        def step(progress):
            self.shown = start + (index - start) * progress
            self.lay_out()
            self.update()

        def done():
            self._slide = None
            self.shown = float(index)
            self.lay_out()

        if motion.on() and not self.tile.dock.testing:
            self._slide = motion.run(self, motion.SLIDE_MS, step, done)
        else:
            done()
            self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta()
        self._wheel += -(delta.y() or delta.x()) / 120
        if abs(self._wheel) >= 1:
            self.go(self.index + (1 if self._wheel > 0 else -1))
            self._wheel = 0.0
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.position().y() < _MediaPage.HEIGHT:
            return
        for number, dot in enumerate(self.dot_rects()):
            if dot.adjusted(-6, -6, 6, 6).contains(event.position()):
                self.go(number)

    def dot_rects(self) -> list[QRectF]:
        count = len(self.pages)
        width = count * 6 + (count - 1) * 6
        left = (self.width() - width) / 2
        return [QRectF(left + i * 12, _MediaPage.HEIGHT + 8, 6, 6) for i in range(count)]

    def paintEvent(self, _):
        if len(self.pages) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for number, dot in enumerate(self.dot_rects()):
            colour = self.look.c(self.look.text)
            colour.setAlpha(230 if number == self.index else 70)
            painter.setBrush(colour)
            painter.drawEllipse(dot)

    def seek(self, session, seconds: float) -> None:
        """Jump there; shown at once, the next look confirms it."""
        from dataclasses import replace
        import time as clock
        for page in self.pages:
            if page.session.id == session.id:
                page.show_session(replace(page.session, position=seconds, stamp=clock.time()))
        if not self.tile.dock.testing:
            from . import media
            media.media().seek(session.id, seconds)

    def command(self, session, kind: str) -> None:
        if self.tile.dock.testing:
            return
        from . import media
        media.media().command(session.id, kind)
        if kind == 'toggle':                               # shown at once; the next look confirms it
            from dataclasses import replace
            for page in self.pages:
                if page.session.id == session.id:
                    page.show_session(replace(page.session, playing=not page.session.playing))


class MediaTile(Tile):
    ID, NAME, GROUP = 'media', 'Media', 'Media & Audio'
    ABOUT = 'One card for each app playing sound. Scroll between them to play, pause or skip.'

    def setup(self):
        self.sessions = []
        self.carousel = None
        if self.dock.testing:
            from .media import Session
            self.sessions = [Session('sample.music', 'Music', 'Song title', 'Artist', True, True, True, True, b'',
                                     74.0, 213.0, 0.0, True),
                             Session('sample.video', 'Browser', 'Video title', 'Channel', False, True, False, False)]
            return
        from . import media
        self.source = media.media()
        self.source.changed.connect(self.sessions_changed)
        self.sessions = list(self.source.sessions)
        self.animate()

    def retire(self):
        if not self.dock.testing:
            try:
                self.source.changed.disconnect(self.sessions_changed)
            except (RuntimeError, TypeError):
                pass

    def sessions_changed(self, sessions):
        self.sessions = sessions
        self.update()
        self.animate()
        if self.carousel is not None:
            try:
                self.carousel.show_sessions(sessions)
                self.empty.setVisible(not sessions)
                self.carousel.setVisible(bool(sessions))
                if self.card is not None:
                    self.card.refit()
            except RuntimeError:
                self.carousel = None

    def playing(self) -> bool:
        return any(s.playing for s in self.sessions)

    BARS = (0.0, 1.9, 0.8, 2.7)          # each bar's phase: they rise and fall out of step
    FRAME_MS = 33                         # about 30 frames a second, only while something plays

    def animate(self) -> None:
        """Equaliser bars that move while something plays; the note while nothing does."""
        if not hasattr(self, 'beat'):
            self.beat = QTimer(self)
            self.beat.setInterval(self.FRAME_MS)
            self.beat.timeout.connect(self.update)
        moving = self.playing() and motion.on() and not self.dock.testing
        if moving and not self.beat.isActive():
            self.beat.start()
        elif not moving and self.beat.isActive():
            self.beat.stop()

    def hideEvent(self, event):
        if hasattr(self, 'beat'):
            self.beat.stop()                     # tucked away or full screen: no frames for nobody
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.animate()

    def paint(self, painter, rect, look):
        if not self.playing():
            L.glyph(painter, rect, MUSIC, look.c(look.text), rect.height() * 0.36)
        else:
            import math
            now = time.perf_counter() if (self.dock.testing is False and motion.on()) else 0.6
            side = rect.height() * 0.4
            bar = side / 5.2
            gap = (side - 4 * bar) / 3
            left = rect.center().x() - side / 2
            bottom = rect.center().y() + side / 2
            painter.setPen(Qt.NoPen)
            painter.setBrush(look.c(look.text))
            for number, phase in enumerate(self.BARS):
                level = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(now * (5.2 + number * 0.9) + phase))
                height = side * level
                painter.drawRoundedRect(QRectF(left + number * (bar + gap), bottom - height, bar, height),
                                        bar / 2, bar / 2)
        playing = next((s for s in self.sessions if s.playing), None)
        self.setToolTip(f'{playing.title} - {playing.app}' if playing and playing.title else 'Media')

    def click(self, pos):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # placed in the card before either is shown or hidden: shown while still without a parent,
        # the carousel came up for a moment as a blank window of its own in the middle of the screen
        self.empty = _label('No media playing.', 'sub')
        layout.addWidget(self.empty)
        self.carousel = _MediaCarousel(self, self.sessions, content)
        layout.addWidget(self.carousel)
        self.empty.setVisible(not self.sessions)
        self.carousel.setVisible(bool(self.sessions))
        if not self.dock.testing:
            self.source.quick(True)
        self.open_card(content, 340)

    def card_closed(self):
        self.carousel = None
        if not self.dock.testing:
            self.source.quick(False)
        super().card_closed()


class VolumeTile(Tile):
    ID, NAME, GROUP = 'volume', 'Volume', 'Media & Audio'
    ABOUT = 'Volume, the switch between speakers and headphones, and the microphone. Scroll to change the volume.'
    WIDTH = 1.5

    def setup(self):
        self.level = None
        self.muted = False
        self.count = 0
        self.read()

    def read(self):
        self.level = audio.volume()
        self.muted = bool(audio.output_muted())
        self.update()

    def tick(self):
        self.count += 1
        if self.count % 2 == 0 and not self.card:
            self.read()

    def paint(self, painter, rect, look):
        if self.level is None:
            self.icon_and_lines(painter, rect, 'mute', '-')
            return
        silent = self.muted or self.level == 0
        self.icon_and_lines(painter, rect, 'mute' if silent else 'volume', f'{self.level}')

    def wheel(self, steps):
        if self.level is None:
            return
        audio.set_volume(max(0, min(100, self.level + round(steps * 2))))
        self.read()

    def click(self, pos):
        self.read()
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(_label('Sound', 'title'))
        row = QHBoxLayout()
        row.setSpacing(10)
        mute = _button(L.G['mute'] if self.muted else L.G['volume'], name='icon', checkable=True)
        mute.setChecked(self.muted)
        mute.setToolTip('Mute')
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(self.level or 0)
        slider.setFocusPolicy(Qt.NoFocus)
        value = QLabel(f'{self.level or 0}')
        value.setFixedWidth(28)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        def moved(level):
            audio.set_volume(level)
            value.setText(str(level))
            mute.setChecked(False)
            mute.setText(L.G['volume'])
            self.level, self.muted = level, False
            self.update()

        def toggled(on):
            audio.mute_output(on)
            mute.setText(L.G['mute'] if on else L.G['volume'])
            self.muted = on
            self.update()

        slider.valueChanged.connect(moved)
        mute.toggled.connect(toggled)
        row.addWidget(mute)
        row.addWidget(slider, 1)
        row.addWidget(value)
        layout.addLayout(row)
        # Hanami's switch: the speakers and the headphones as two icons, the one in use lit; and the mic
        controls = QHBoxLayout()
        controls.setSpacing(8)
        speakers, phones = sound_sides()
        now = audio.current()
        if speakers and phones:
            switch = OutputSwitch(self.look, speakers, phones, now)
            switch.flipped.connect(lambda _: self.read())
            controls.addWidget(switch)
        mic = _button('\ue720', name='icon', checkable=True)
        mic_state = audio.mic_muted()
        mic.setEnabled(mic_state is not None)
        mic.setChecked(bool(mic_state))
        mic.setToolTip('Microphone muted' if mic_state else 'Mute microphone')
        mic.toggled.connect(lambda on: (audio.mute_mic(on),
                                        mic.setToolTip('Microphone muted' if on else 'Mute microphone')))
        controls.addWidget(mic)
        controls.addStretch(1)
        controls.addWidget(_button('Sound settings', lambda: (self.card and self.card.close(),
                                                              _open('ms-settings:sound'))))
        layout.addLayout(controls)
        apps = self.app_rows()
        if apps is not None:
            layout.addWidget(apps)
        self.open_card(content, 340)

    APPS_SHOWN = 5          # rows before the list scrolls

    def app_rows(self) -> QWidget | None:
        """Each app's volume, as Windows' Volume mixer: its icon and name, mute, and a slider."""
        rows = audio.app_volumes() if not self.dock.testing else [
            audio.AppVolume('', 0, '', 60, False, True), audio.AppVolume('c:\\sample\\music.exe', 1, 'Music', 80, False)]
        if not rows:
            return None
        host = QWidget()
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 4, 0, 0)
        column.setSpacing(8)
        column.addWidget(_label('Apps', 'sub'))
        holder = QWidget()
        lines = QVBoxLayout(holder)
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(6)
        provider = QFileIconProvider()
        for row in rows:
            lines.addLayout(self.app_row(row, provider))
        if len(rows) > self.APPS_SHOWN:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QScrollArea.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            area.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }')
            area.setWidget(holder)
            area.setFixedHeight(self.APPS_SHOWN * 40)
            column.addWidget(area)
        else:
            column.addWidget(holder)
        return host

    def app_row(self, row, provider) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setSpacing(8)
        icon = QLabel()
        icon.setFixedSize(22, 22)
        if row.system:
            icon.setText(L.G['volume'])
            icon.setStyleSheet(f"font-family: '{L.icon_family()}'; font-size: 15px;")
            icon.setAlignment(Qt.AlignCenter)
        elif row.key and not row.key.startswith('pid:'):
            icon.setPixmap(provider.icon(QFileInfo(row.key)).pixmap(QSize(20, 20), self.devicePixelRatioF()))
        name = 'System sounds' if row.system else (row.name or win.program_name(row.key))
        label = QLabel(name)
        label.setFixedWidth(96)
        label.setToolTip(name)
        mute = _button(L.G['mute'] if row.muted else L.G['volume'], name='icon', checkable=True)
        mute.setChecked(row.muted)
        mute.setToolTip('Mute')
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(row.volume)
        slider.setFocusPolicy(Qt.NoFocus)
        value = QLabel(str(row.volume))
        value.setFixedWidth(26)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        def moved(level, key=row.key):
            if not self.dock.testing:
                audio.set_app_volume(key, percent=level, muted=False if mute.isChecked() else None)
            value.setText(str(level))
            if mute.isChecked():
                mute.blockSignals(True)
                mute.setChecked(False)
                mute.setText(L.G['volume'])
                mute.blockSignals(False)

        def toggled(on, key=row.key):
            if not self.dock.testing:
                audio.set_app_volume(key, muted=on)
            mute.setText(L.G['mute'] if on else L.G['volume'])

        slider.valueChanged.connect(moved)
        mute.toggled.connect(toggled)
        line.addWidget(icon)
        line.addWidget(label)
        line.addWidget(mute)
        line.addWidget(slider, 1)
        line.addWidget(value)
        return line


class OutputSwitch(QWidget):
    """Hanami's speakers/headphones switch: a capsule holding both icons, the one the sound goes to
    lit. A click anywhere on it sends the sound to the other."""

    flipped = Signal(str)

    def __init__(self, look, speakers, phones, now: str):
        super().__init__()
        self.look = look
        self.sides = (speakers, phones)                  # each (Windows' id, its name)
        self.on = 1 if now == phones[0] else 0
        self.shown = float(self.on)                      # where the light is, sliding between the two
        self.setFixedSize(76, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f'Speakers: {speakers[1]}\nHeadphones: {phones[1]}')

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self.rect().contains(event.position().toPoint()):
            return
        self.on = 1 - self.on
        audio.choose(self.sides[self.on][0])
        start = self.shown

        def step(progress, start=start):
            self.shown = start + (self.on - start) * progress
            self.update()

        motion.run(self, motion.HOVER_MS, step)
        self.flipped.emit(self.sides[self.on][0])

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        look = self.look
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        painter.setPen(QPen(look.c(look.edge), 1))
        painter.setBrush(look.c(look.tile) if look.c(look.tile).alpha() else look.c(look.hover))
        painter.drawRoundedRect(rect, radius, radius)
        half = rect.width() / 2
        light = QRectF(rect.x() + 3 + self.shown * (half - 3), rect.y() + 3, half - 3, rect.height() - 6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(L.chip(look))
        painter.drawRoundedRect(light, light.height() / 2, light.height() / 2)
        halo, L.HALO = L.HALO, None
        try:
            for index, icon in enumerate(('\ue7f5', '\ue7f6')):      # speakers, headphones
                box = QRectF(rect.x() + index * half, rect.y(), half, rect.height())
                lit = abs(self.shown - index) < 0.5
                L.glyph(painter, box, icon, L.on_chip(look) if lit else look.c(look.text), 15)
        finally:
            L.HALO = halo


_PHONE_WORDS = ('headphone', 'headset', 'earbud', 'earphone', 'airpods')


def sound_sides():
    """The speakers and the headphones, as (id, name) each or None: those chosen in Hanami if they
    are plugged in, else guessed as Hanami guesses - headphones by name, speakers the output in use or
    the first other one."""
    try:
        present = audio.outputs()
    except OSError:
        return None, None
    ids = {o.id: o for o in present}
    chosen = {}
    try:
        import json
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Hanami\Hanami\sound') as key:
            for which in ('speakers', 'headphones'):
                try:
                    value = json.loads(winreg.QueryValueEx(key, which)[0])
                except (OSError, ValueError):
                    continue
                if isinstance(value, list) and len(value) == 2 and value[0] in ids:
                    chosen[which] = (value[0], ids[value[0]].name)
    except OSError:
        pass
    if 'headphones' not in chosen:
        phones = next((o for o in present if any(w in o.name.lower() for w in _PHONE_WORDS)), None)
        if phones:
            chosen['headphones'] = (phones.id, phones.name)
    if 'speakers' not in chosen:
        taken = chosen.get('headphones', ('',))[0]
        now = audio.current()
        others = [o for o in present if o.id != taken]
        pick = next((o for o in others if o.id == now), others[0] if others else None)
        if pick:
            chosen['speakers'] = (pick.id, pick.name)
    return chosen.get('speakers'), chosen.get('headphones')


# ================================================================ Productivity

class TimerTile(Tile):
    ID, NAME, GROUP = 'timer', 'Timer', 'Productivity'
    ABOUT = 'A countdown timer.'
    WIDTH = 2.15
    PRESETS = (1, 3, 5, 10, 15, 25, 45, 60)

    def setup(self):
        self.length = 5 * 60
        self.ends: float | None = None
        self.left = float(self.length)
        self.finished_at: float | None = None
        self.big = None

    def remaining(self) -> float:
        return max(0.0, self.ends - time.monotonic()) if self.ends else self.left

    def tick(self):
        if self.ends and self.remaining() <= 0:
            self.ends = None
            self.left = float(self.length)
            self.finished_at = time.monotonic()
            ctypes.windll.user32.MessageBeep(0x40)
            self.dock.alert()
        if self.finished_at and time.monotonic() - self.finished_at > 10:
            self.finished_at = None
        if self.big is not None:
            try:
                self.big.setText(_clock(self.remaining()))
            except RuntimeError:
                self.big = None
        self.update()

    def paint(self, painter, rect, look):
        if self.finished_at and int(time.monotonic() * 2) % 2 == 0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(L.chip(look))
            painter.drawRoundedRect(rect, 10, 10)
        running = self.ends is not None
        main = _clock(self.remaining()) if running or self.left < self.length else 'Timer'
        sub = 'Done' if self.finished_at else ('Running' if running else ('Paused' if self.left < self.length
                                                                          else _clock(self.length)))
        self.icon_and_lines(painter, rect, 'timer', main, sub)

    def start(self, minutes: int | None = None):
        if minutes:
            self.length = minutes * 60
            self.left = float(self.length)
        self.ends = time.monotonic() + self.left
        self.finished_at = None
        self.tick()

    def pause(self):
        if self.ends:
            self.left = self.remaining()
            self.ends = None
        self.tick()

    def reset(self):
        self.ends = None
        self.left = float(self.length)
        self.tick()

    def click(self, pos):
        if self.finished_at:
            self.finished_at = None
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(_label('Timer', 'title'))
        self.big = _label(_clock(self.remaining()), 'big')
        layout.addWidget(self.big)
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, minutes in enumerate(self.PRESETS):
            grid.addWidget(_button(f'{minutes} min', lambda _=False, m=minutes: self.start(m)), i // 4, i % 4)
        layout.addLayout(grid)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(_button('Start', lambda: self.start(), 'primary'))
        row.addWidget(_button('Pause', self.pause))
        row.addWidget(_button('Reset', self.reset))
        layout.addLayout(row)
        self.open_card(content, 320)

    def card_closed(self):
        self.big = None
        super().card_closed()


class StopwatchTile(Tile):
    ID, NAME, GROUP = 'stopwatch', 'Stopwatch', 'Productivity'
    ABOUT = 'A stopwatch with laps.'
    WIDTH = 2.35

    def setup(self):
        self.started: float | None = None
        self.banked = 0.0
        self.laps: list[float] = []
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.refresh)
        self.big = None
        self.lap_list = None

    def elapsed(self) -> float:
        return self.banked + (time.perf_counter() - self.started if self.started else 0.0)

    def refresh(self):
        if self.big is not None:
            try:
                self.big.setText(_clock(self.elapsed(), True))
            except RuntimeError:
                self.big = None
        self.update()

    def paint(self, painter, rect, look):
        running = self.started is not None
        main = _clock(self.elapsed(), True)
        sub = 'Running' if running else ('Paused' if self.banked else 'Stopwatch')
        self.icon_and_lines(painter, rect, 'stopwatch', main, sub)

    def toggle(self):
        if self.started is None:
            self.started = time.perf_counter()
            self.timer.start()
        else:
            self.banked = self.elapsed()
            self.started = None
            self.timer.stop()
        self.refresh()

    def lap(self):
        if self.started is not None:
            self.laps.append(self.elapsed())
            self.show_laps()

    def reset(self):
        self.started = None
        self.banked = 0.0
        self.laps.clear()
        self.timer.stop()
        self.show_laps()
        self.refresh()

    def show_laps(self):
        if self.lap_list is None:
            return
        try:
            layout = self.lap_list.layout()
            while layout.count():
                item = layout.takeAt(0)
                old_w = item.widget()
                if old_w is not None:
                    old_w.hide()
                    old_w.setParent(None)
                    old_w.deleteLater()
            previous = 0.0
            for number, total in reversed(list(enumerate(self.laps, 1))):
                index = number - 1
                previous = self.laps[index - 1] if index else 0.0
                row = QLabel(f'Lap {number}    {_clock(total - previous, True)}    {_clock(total, True)}')
                row.setObjectName('sub')
                layout.addWidget(row)
            self.lap_list.setVisible(bool(self.laps))
            if self.card:
                self.card.refit()
        except RuntimeError:
            self.lap_list = None

    def click(self, pos):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(_label('Stopwatch', 'title'))
        self.big = _label(_clock(self.elapsed(), True), 'big')
        layout.addWidget(self.big)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(_button('Start or pause', self.toggle, 'primary'))
        row.addWidget(_button('Lap', self.lap))
        row.addWidget(_button('Reset', self.reset))
        layout.addLayout(row)
        self.lap_list = QWidget()
        lap_layout = QVBoxLayout(self.lap_list)
        lap_layout.setContentsMargins(0, 0, 0, 0)
        lap_layout.setSpacing(4)
        layout.addWidget(self.lap_list)
        self.show_laps()
        self.open_card(content, 320)

    def card_closed(self):
        self.big = None
        self.lap_list = None
        super().card_closed()


class PomodoroTile(Tile):
    ID, NAME, GROUP = 'pomodoro', 'Pomodoro', 'Productivity'
    ABOUT = 'Focus rounds of 25 minutes with breaks between them.'
    WIDTH = 2.5
    FOCUS, BREAK, LONG = 25 * 60, 5 * 60, 15 * 60

    def setup(self):
        self.phase = 'Focus'
        self.round = 1
        self.length = self.FOCUS
        self.left = float(self.FOCUS)
        self.ends: float | None = None
        self.big = None
        self.phase_label = None

    def remaining(self) -> float:
        return max(0.0, self.ends - time.monotonic()) if self.ends else self.left

    def next_phase(self, sound: bool = True):
        if self.phase == 'Focus':
            self.phase = 'Long break' if self.round % 4 == 0 else 'Break'
            self.length = self.LONG if self.round % 4 == 0 else self.BREAK
        else:
            self.phase = 'Focus'
            self.round = self.round % 4 + 1
            self.length = self.FOCUS
        self.left = float(self.length)
        running = self.ends is not None
        self.ends = time.monotonic() + self.left if running else None
        if sound:
            ctypes.windll.user32.MessageBeep(0x40)
            self.dock.alert()

    def tick(self):
        if self.ends and self.remaining() <= 0:
            self.next_phase()
        if self.big is not None:
            try:
                self.big.setText(_clock(self.remaining()))
                self.phase_label.setText(f'{self.phase} · round {self.round} of 4')
            except RuntimeError:
                self.big = None
        self.update()

    def paint(self, painter, rect, look):
        share = 1 - self.remaining() / self.length if self.length else 0
        idle = self.ends is None and self.left == self.length and self.phase == 'Focus' and self.round == 1
        self.ring_and_lines(painter, rect, share, _clock(self.remaining()),
                            'Pomodoro' if idle else (self.phase if self.ends else 'Paused'))

    def toggle(self):
        if self.ends:
            self.left = self.remaining()
            self.ends = None
        else:
            self.ends = time.monotonic() + self.left
        self.tick()

    def skip(self):
        self.next_phase(sound=False)
        self.tick()

    def reset(self):
        self.setup()
        self.update()

    def click(self, pos):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(_label('Pomodoro', 'title'))
        self.big = _label(_clock(self.remaining()), 'big')
        layout.addWidget(self.big)
        self.phase_label = _label(f'{self.phase} · round {self.round} of 4', 'sub')
        layout.addWidget(self.phase_label)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(_button('Start or pause', self.toggle, 'primary'))
        row.addWidget(_button('Skip', self.skip))
        row.addWidget(_button('Reset', lambda: (self.reset(), self.tick())))
        layout.addLayout(row)
        self.open_card(content, 320)

    def card_closed(self):
        self.big = None
        self.phase_label = None
        super().card_closed()


class NotesTile(Tile):
    ID, NAME, GROUP = 'notes', 'Notes', 'Productivity'
    ABOUT = 'A note. Click to edit it.'
    WIDTH = 2.35

    def setup(self):
        self.saver = QTimer(self)
        self.saver.setSingleShot(True)
        self.saver.setInterval(600)
        self.saver.timeout.connect(self.dock.cfg.save)

    def paint(self, painter, rect, look):
        first = next((line.strip() for line in self.dock.cfg['notes'].splitlines() if line.strip()), '')
        self.icon_and_lines(painter, rect, 'note', first or 'Notes', '' if first else 'Empty')

    def click(self, pos):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(_label('Notes', 'title'))
        editor = QPlainTextEdit(self.dock.cfg['notes'])
        editor.setPlaceholderText('Write a note')
        editor.setFixedHeight(220)

        def changed():
            self.dock.cfg['notes'] = editor.toPlainText()
            self.saver.start()
            self.update()

        editor.textChanged.connect(changed)
        layout.addWidget(editor)
        self.open_card(content, 360)
        editor.setFocus()
        editor.moveCursor(QTextCursor.MoveOperation.End)


# ================================================================ Clocks

class _Month(QWidget):
    """A month as a grid of days, Monday first, today marked."""

    def __init__(self, look: L.Look):
        super().__init__()
        self.look = look
        today = dt.date.today()
        self.year, self.month = today.year, today.month
        self.setFixedSize(7 * 38, 7 * 32)

    def shift(self, months: int):
        index = self.year * 12 + self.month - 1 + months
        self.year, self.month = divmod(index, 12)
        self.month += 1
        self.update()

    def title(self) -> str:
        return f'{calendar.month_name[self.month]} {self.year}'

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        look = self.look
        for column, name in enumerate(('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su')):
            L.text(painter, QRectF(column * 38, 0, 38, 32), name, look.c(look.sub), 12, 600)
        today = dt.date.today()
        weeks = calendar.Calendar(0).monthdatescalendar(self.year, self.month)
        for row, week in enumerate(weeks[:6]):
            for column, day in enumerate(week):
                box = QRectF(column * 38, (row + 1) * 32, 38, 32)
                colour = look.c(look.text) if day.month == self.month else look.c(look.track)
                if day == today:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(L.chip(look))
                    painter.drawEllipse(box.center(), 14, 14)
                    colour = L.on_chip(look)
                L.text(painter, box, str(day.day), colour, 13, 600 if day == today else 400)


class ClockTile(Tile):
    ID, NAME, GROUP = 'clock', 'Clock', 'Clocks'
    ABOUT = 'The time and date, in 12- or 24-hour format. A click shows the month.'
    WIDTH = 2.15

    def setup(self):
        self.big = None                          # the open card's time, with seconds
        self.seconds = QTimer(self)
        self.seconds.setSingleShot(True)
        self.seconds.setTimerType(Qt.PreciseTimer)
        self.seconds.timeout.connect(self.next_second)

    def tick(self):
        self.update()

    def time_text(self, now: dt.datetime, seconds: bool = False) -> str:
        if self.dock.cfg['clock24']:
            return now.strftime('%H:%M:%S' if seconds else '%H:%M')
        return now.strftime('%I:%M:%S %p' if seconds else '%I:%M %p').lstrip('0')

    def next_second(self):
        """The card's clock turns over as each second does: woken just after it begins."""
        if self.big is None:
            return
        now = dt.datetime.now()
        try:
            self.big.setText(self.time_text(now, True))
        except RuntimeError:
            self.big = None
            return
        self.seconds.start(1000 - now.microsecond // 1000 + 5)

    def paint(self, painter, rect, look):
        now = dt.datetime.now()
        self.lines(painter, rect, self.time_text(now), f'{now:%a}, {now:%b} {now.day}', Qt.AlignHCenter)

    def click(self, pos):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        now = dt.datetime.now()
        self.big = _label(self.time_text(now, True), 'big')
        layout.addWidget(self.big)
        layout.addWidget(_label(f'{now:%A}, {now:%B} {now.day}, {now.year}', 'title'))
        month = _Month(self.look)
        header = QHBoxLayout()
        title = _label(month.title())
        header.addWidget(title, 1)
        for glyph, step in (('left', -1), ('right', 1)):
            header.addWidget(_button(L.G[glyph], lambda _=False, s=step: (month.shift(s),
                                                                          title.setText(month.title())), 'icon'))
        layout.addLayout(header)
        layout.addWidget(month, 0, Qt.AlignHCenter)
        self.open_card(content, 7 * 38 + 2 * 16 + 2)
        if self.card:
            self.next_second()
        else:
            self.big = None

    def card_closed(self):
        self.big = None
        self.seconds.stop()
        super().card_closed()

    def retire(self):
        self.big = None
        self.seconds.stop()


# ================================================================ Weather

class WeatherTile(Tile):
    ID, NAME, GROUP = 'weather', 'Weather', 'Weather'
    ABOUT = 'Temperature and conditions for the city set in Settings, with a three-day forecast.'
    WIDTH = 2.25
    EVERY = 15 * 60

    def setup(self):
        self.count = 0
        self.dock.weather.changed.connect(self.weather_changed)
        self.fetch()

    def retire(self):
        try:
            self.dock.weather.changed.disconnect(self.weather_changed)
        except (RuntimeError, TypeError):
            pass

    def weather_changed(self, _):
        self.update()

    def fetch(self):
        cfg = self.dock.cfg
        if cfg['latitude'] is not None and not self.dock.testing:
            self.dock.weather.fetch(cfg['latitude'], cfg['longitude'], cfg['fahrenheit'])

    def tick(self):
        self.count += 1
        if self.count % self.EVERY == 0 or (self.dock.weather.latest is None and self.count % 60 == 0):
            self.fetch()

    def paint(self, painter, rect, look):
        data = self.dock.weather.latest
        if self.dock.cfg['latitude'] is None:
            self.icon_and_lines(painter, rect, 'sun', 'Weather', 'Set city')
        elif data is None:
            self.icon_and_lines(painter, rect, 'cloud', '--', self.dock.cfg['city'])
        else:
            words, kind = weather.condition(data['code'])
            self.icon_and_lines(painter, rect, kind, f'{round(data["temperature"])}°', words)

    def click(self, pos):
        cfg = self.dock.cfg
        if cfg['latitude'] is None:
            self.dock.open_settings()
            return
        data = self.dock.weather.latest
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(_label(cfg['city'], 'title'))
        if data is None:
            layout.addWidget(_label('Weather data unavailable. Check the network connection.', 'sub'))
            self.fetch()
            self.open_card(content, 300)
            return
        words, _ = weather.condition(data['code'])
        layout.addWidget(_label(f'{round(data["temperature"])}°', 'big'))
        unit = 'mph' if cfg['fahrenheit'] else 'km/h'
        details = words
        if data.get('wind') is not None:
            details += f' · Wind {round(data["wind"])} {unit}'
        if data.get('humidity') is not None:
            details += f' · Humidity {data["humidity"]}%'
        layout.addWidget(_label(details, 'sub'))
        layout.addSpacing(12)
        for day in data['days'][:4]:
            date = dt.date.fromisoformat(day['date'])
            row = QHBoxLayout()
            name = QLabel('Today' if date == dt.date.today() else f'{date:%A}')
            icon = _Glyph(weather.condition(day['code'])[1], self.look, 16)
            icon.setFixedSize(28, 22)
            high_low = QLabel(f'{round(day["high"])}°  /  {round(day["low"])}°')
            row.addWidget(name, 1)
            row.addWidget(icon)
            row.addWidget(high_low)
            layout.addLayout(row)
        self.open_card(content, 300)


# ================================================================ AI

class _UsageRingCell(QWidget):
    """One limit window inside an AI popup card: label on top, circular progress ring, and reset timer."""

    HEIGHT = 96

    def __init__(self, look: L.Look, window: dict, show_used: bool, clock24: bool):
        super().__init__()
        self.look = look
        self.window = window
        self.show_used = show_used
        self.clock24 = clock24
        self.setFixedHeight(self.HEIGHT)
        self._sync_tooltip()

    def _sync_tooltip(self) -> None:
        pct, _share, left = ai.shown_value(self.window, show_used=self.show_used)
        word = 'used' if self.show_used else 'left'
        is_idle = self.window.get('resets_at') is None and left >= 99.9
        long_reset = ai.format_reset_long(self.window.get('resets_at'), clock24=self.clock24, idle=is_idle)
        detail = self.window.get('detail')
        head = f"{detail} · {self.window.get('label', '')}" if detail else self.window.get('label', '')
        self.setToolTip(f'{head} — {pct}% {word} · {long_reset}')

    def set_mode(self, show_used: bool) -> None:
        self.show_used = show_used
        self._sync_tooltip()
        self.update()

    def set_window(self, window: dict) -> None:
        self.window = window
        self._sync_tooltip()
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        look = self.look
        w = self.width()
        pct, share, left = ai.shown_value(self.window, show_used=self.show_used)
        is_idle = self.window.get('resets_at') is None and left >= 99.9
        reset_text = ai.format_reset_card(self.window.get('resets_at'), idle=is_idle)

        L.text(painter, QRectF(0, 0, w, 18), self.window.get('label', ''), look.c(look.text), 12, 600,
               Qt.AlignCenter)

        ring_d = 50.0
        stroke = 4.6
        ring_rect = QRectF((w - ring_d) / 2.0, 22.0, ring_d, ring_d)
        track_col = look.c(look.track)
        if track_col.alpha() < (68 if look.dark else 56):
            track_col.setAlpha(68 if look.dark else 56)
        L.ring(painter, ring_rect, share, ai.health_color(left, look.dark), track_col, stroke)
        L.text(painter, ring_rect, f'{pct}%', look.c(look.text), 13, 600, Qt.AlignCenter)

        if reset_text:
            L.text(painter, QRectF(0, 76, w, 18), reset_text, look.c(look.sub), 11, 400, Qt.AlignCenter)


class _Glyph(QWidget):
    """One of the dock's icons, drawn as the dock draws it (L.glyph: the icon font's, or one drawn
    where the font has none)."""

    def __init__(self, name: str, look, pixels: float):
        super().__init__()
        self.name, self.look, self.pixels = name, look, pixels

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        L.glyph(painter, QRectF(self.rect()), self.name, self.look.c(self.look.text), self.pixels)


def _money(amount: float, currency: str = 'USD') -> str:
    sign = '$' if currency.upper() == 'USD' else f'{currency.upper()} '
    return f'{sign}{amount:,.2f}'


def _cost_lines(state: dict) -> list[str]:
    """A service's money lines on the AI card: its usage credits (spent this month, of the limit, or
    off), and what its use in the plan's windows would cost at API prices (Claude Code on this PC)."""
    lines = []
    estimate = state.get('estimate') or {}
    parts = [f'{_money(estimate[key])} {name}' for key, name in (('session', 'this session'),
                                                                   ('weekly', 'this week')) if key in estimate]
    if parts:
        lines.append('Estimated API cost: ' + ', '.join(parts) + '. Claude Code on this PC, at API prices.')
    spend = state.get('spend')
    if isinstance(spend, dict) and spend.get('used') is not None:
        currency = spend.get('currency') or 'USD'
        used = _money(spend['used'], currency)
        if not spend.get('enabled'):
            lines.append(f'Usage credits: Off ({used} used this month)')
        elif spend.get('limit'):
            lines.append(f'Usage credits: {used} of {_money(spend["limit"], currency)} used this month')
        else:
            lines.append(f'Usage credits: {used} used this month')
    return lines


class _AITile(Tile):
    GROUP = 'AI'
    PROVIDER = ''
    MIN_WIDTH = 1.9

    def setup(self):
        self._ticks = 0
        self._preview = False
        self._last_sig: tuple = ()
        self._card_shape: tuple = ()
        self._card_rows: list[tuple[str, int, _UsageRingCell]] = []
        self._mode_sub: QLabel | None = None
        self._providers_box: QVBoxLayout | None = None
        self._card_multi = False
        self.dock.ai.changed.connect(self._ai_changed)
        self.dock.ai.busy_changed.connect(self._ai_busy_changed)
        if not self.dock.testing:
            self.dock.ai.ensure_watching()
            self.dock.ai.poke(max_age=15.0)

    def retire(self):
        for sig, slot in ((self.dock.ai.changed, self._ai_changed),
                          (self.dock.ai.busy_changed, self._ai_busy_changed)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def enabled_providers(self) -> list[str]:
        raw = self.dock.cfg.get('ai_enabled')
        chosen = [p for p in (raw if isinstance(raw, list) else ai.PROVIDERS) if p in ai.PROVIDERS]
        return chosen or list(ai.PROVIDERS)

    def active_card_providers(self) -> tuple[str, ...]:
        if self._card_multi:
            return tuple(self.enabled_providers())
        return (self.PROVIDER,) if self.PROVIDER else ai.PROVIDERS

    def _shape_for(self, snap: dict[str, dict], providers: tuple[str, ...]) -> tuple:
        out = []
        for p in providers:
            st = snap.get(p) or {}
            wins = st.get('windows') or []
            out.append((p, bool(st.get('connected') and wins), len(wins), st.get('plan', ''), st.get('error', '')))
        return tuple(out)

    def _ai_changed(self, _):
        self.update()
        self._sync_open_card()

    def _ai_busy_changed(self, _busy: bool):
        if self.card is not None and self._mode_sub is not None:
            try:
                self._mode_sub.setText(self._subtitle_text(self.snapshot(), self.active_card_providers()))
            except RuntimeError:
                self._mode_sub = None

    def _sync_open_card(self) -> None:
        if self.card is None:
            return
        snap = self.snapshot()
        providers = self.active_card_providers()
        if self._providers_box is not None and self._shape_for(snap, providers) != self._card_shape:
            self._populate_provider_rows(providers)
            self.card.refit()
            return
        show_used = bool(self.dock.cfg.get('ai_show_used', False))
        for provider, idx, row_widget in list(self._card_rows):
            windows = snap.get(provider, {}).get('windows') or []
            if idx < len(windows):
                try:
                    row_widget.set_window(windows[idx])
                    row_widget.set_mode(show_used)
                except RuntimeError:
                    pass
        if self._mode_sub is not None:
            try:
                self._mode_sub.setText(self._subtitle_text(snap, providers))
            except RuntimeError:
                self._mode_sub = None

    def _tick_open_card(self) -> None:
        if self.card is None:
            return
        snap = self.snapshot()
        providers = self.active_card_providers()
        if self._mode_sub is not None:
            try:
                sub = self._subtitle_text(snap, providers)
                if self._mode_sub.text() != sub:
                    self._mode_sub.setText(sub)
            except RuntimeError:
                self._mode_sub = None
        for _provider, _idx, row_widget in list(self._card_rows):
            try:
                row_widget.update()
            except RuntimeError:
                pass

    def card_closed(self):
        self._card_rows = []
        self._card_shape = ()
        self._mode_sub = None
        self._providers_box = None
        super().card_closed()

    def _visual_signature(self) -> tuple:
        snap = self.snapshot()
        show_used = bool(self.dock.cfg.get('ai_show_used', False))
        providers = (self.PROVIDER,) if self.PROVIDER else tuple(self.enabled_providers())
        sig: list = [show_used, self._tile_style()]
        for p in providers:
            st = snap.get(p) or {}
            if st.get('connected'):
                for w in (st.get('windows') or ())[:2]:
                    pct, _sh, _left = ai.shown_value(w, show_used=show_used)
                    sig.append((p, pct, ai.format_reset(w.get('resets_at'))))
            else:
                sig.append((p, st.get('error', '')))
        return tuple(sig)

    def tick(self):
        self._ticks += 1
        if not self.dock.testing:
            card_open = any(isinstance(t, _AITile) and t.card is not None for t in self.dock.tiles)
            self.dock.ai.step(card_open=card_open)
        sig = self._visual_signature()
        if sig != self._last_sig or self._ticks % 10 == 0:
            self._last_sig = sig
            self.update()
        if self.card is not None:
            self._tick_open_card()

    def snapshot(self) -> dict[str, dict]:
        if self.dock.testing:
            return ai.sample_snapshot()
        live = self.dock.ai.latest
        if self._preview:
            if self.PROVIDER:
                if not live.get(self.PROVIDER, {}).get('connected'):
                    return ai.sample_snapshot()
            elif not any(live.get(p, {}).get('connected') for p in self.enabled_providers()):
                return ai.sample_snapshot()
        return live

    def _tile_style(self) -> str:
        val = self.dock.cfg.get('ai_tile_style', 'rings')
        return val if val in ('rings', 'gauges', 'bars') else 'rings'

    def default_units(self) -> float:
        style = self._tile_style()
        if style == 'bars':
            return self.WIDTH + 0.75
        if style == 'gauges':
            return self.WIDTH + 0.35
        return self.WIDTH

    def current_units(self) -> float:
        if self.resizable():
            saved = (self.dock.cfg.get('widget_widths') or {}).get(self.ID)
            if isinstance(saved, (int, float)):
                return max(self.min_units(), min(self.MAX_WIDTH, float(saved)))
        return self.default_units()

    def reset_width(self) -> None:
        widths = dict(self.dock.cfg.get('widget_widths') or {})
        if self.ID in widths:
            widths.pop(self.ID, None)
            self.dock.cfg['widget_widths'] = widths
            self.dock.cfg.save()
        self.setFixedWidth(round(self.dock.tile_height * self.default_units()))
        self.update()
        self.dock.fit_now()

    def _paint_rows(self, painter: QPainter, area: QRectF, look: L.Look,
                    rows: list[tuple[str | None, str, dict]]) -> None:
        """Draw two compact limit rows using circular rings (default), side-by-side gauges, or bars."""
        style = self._tile_style()
        show_used = bool(self.dock.cfg.get('ai_show_used', False))
        if style == 'gauges' and len(rows) >= 2:
            cell_w = area.width() / 2.0
            ring_s = min(area.height() * 0.76, cell_w * 0.42)
            big, small = self.sizes()
            for idx, (provider, label, window) in enumerate(rows[:2]):
                cx = area.x() + idx * cell_w + (4 if idx else 0)
                pct, share, left = ai.shown_value(window, show_used=show_used)
                share = self.eased(('ai', provider, label), share)
                ry = area.center().y() - ring_s / 2.0
                L.ring(painter, QRectF(cx, ry, ring_s, ring_s), share,
                       ai.health_color(left, look.dark), look.c(look.track), max(2.6, ring_s * 0.11))
                if provider:
                    mark_s = ring_s * 0.56
                    ai.paint_provider_mark(painter, QRectF(cx + (ring_s - mark_s) / 2.0,
                                                           ry + (ring_s - mark_s) / 2.0, mark_s, mark_s),
                                           provider, look.dark)
                tx = cx + ring_s + 5
                tw = max(18.0, (area.x() + (idx + 1) * cell_w) - tx - 2)
                sub = ai.format_reset(window.get('resets_at')) or label
                self.lines(painter, QRectF(tx, area.y(), tw, area.height()), f'{pct}%', sub,
                           roll=('ai', provider, label))
            return

        px = {'S': 10, 'M': 11, 'L': 12}.get(self.dock.cfg['size'], 11)
        row_h = px * 1.24
        gap_y = max(2.0, area.height() * 0.08)
        total_h = 2 * row_h + gap_y
        top_y = area.center().y() - total_h / 2
        has_icons = any(p is not None for p, _, _ in rows)
        icon_s = round(px * 1.15) if has_icons else 0
        icon_col = (icon_s + 4) if has_icons else 0
        ring_d = round(px * 1.18)

        fm_bold = QFontMetricsF(L.font(px, 600))
        fm_reg = QFontMetricsF(L.font(px, 400))
        prepared = []
        for provider, label, window in rows[:2]:
            pct, share, left = ai.shown_value(window, show_used=show_used)
            share = self.eased(('ai', provider, label), share)
            pct_str = f'{pct}%'
            reset_str = ai.format_reset(window.get('resets_at'))
            prepared.append((provider, label, pct_str, reset_str, share, left))

        import math
        natural_label_w = math.ceil(max((fm_bold.horizontalAdvance(item[1]) for item in prepared), default=0.0)) + 2
        pct_w = math.ceil(max((fm_bold.horizontalAdvance(item[2]) for item in prepared), default=0.0)) + 2
        reset_w = math.ceil(max((fm_reg.horizontalAdvance(item[3]) for item in prepared if item[3]), default=0.0))
        if reset_w > 0:
            reset_w += 2

        track_col = look.c(look.track)
        if track_col.alpha() < (64 if look.dark else 52):
            track_col.setAlpha(64 if look.dark else 52)

        total_w = area.width()
        for idx, (provider, label, pct_str, reset_str, share, left) in enumerate(prepared):
            y = top_y + idx * (row_h + gap_y)
            x = area.x()
            if has_icons and provider:
                ai.paint_provider_mark(painter, QRectF(x, y + (row_h - icon_s) / 2, icon_s, icon_s),
                                       provider, look.dark)
                x += icon_col
            right = area.right()
            self.roll(('ai', provider, label)).paint(painter, QRectF(right - pct_w, y, pct_w, row_h), pct_str,
                                                     look.c(look.text), px, 600, Qt.AlignRight)
            pct_left = right - pct_w

            if style == 'rings':
                # Compact empty space first; keep the usage circle visible at every width
                avail = pct_left - x
                min_lbl = min(natural_label_w, round(px * 2.5))
                has_reset_room = reset_w > 0 and avail >= min_lbl + 5 + ring_d + 5 + reset_w + 5
                if has_reset_room:
                    reset_right = pct_left - 5
                    if reset_str:
                        L.text(painter, QRectF(reset_right - reset_w, y, reset_w, row_h), reset_str,
                               look.c(look.sub), px, 400, Qt.AlignRight | Qt.AlignVCenter)
                    after_ring_limit = reset_right - reset_w - 5
                else:
                    after_ring_limit = pct_left - 5

                room_for_label_and_ring = after_ring_limit - x
                if room_for_label_and_ring >= ring_d + 22:
                    lbl_w = min(natural_label_w, room_for_label_and_ring - ring_d - 5)
                    L.text(painter, QRectF(x, y, lbl_w, row_h), label, look.c(look.text), px, 600,
                           Qt.AlignLeft | Qt.AlignVCenter)
                    slack = max(0.0, after_ring_limit - (x + lbl_w + 5 + ring_d))
                    rx = x + lbl_w + 5 + min(slack * 0.25, 10.0)
                else:
                    # Ultra-compact: icon + usage ring + percentage
                    rx = x + max(0.0, (room_for_label_and_ring - ring_d) / 2.0)

                L.ring(painter, QRectF(rx, y + (row_h - ring_d) / 2, ring_d, ring_d), share,
                       ai.health_color(left, look.dark), track_col, max(2.2, ring_d * 0.19))
            else:
                avail_mid = (pct_left - 4) - x
                show_reset = reset_w > 0 and avail_mid >= natural_label_w + reset_w + 6
                reset_right = pct_left - 4
                if show_reset:
                    if reset_str:
                        L.text(painter, QRectF(reset_right - reset_w, y, reset_w, row_h), reset_str,
                               look.c(look.sub), px, 400, Qt.AlignRight | Qt.AlignVCenter)
                    ind_right = reset_right - reset_w - 5
                else:
                    ind_right = reset_right
                lbl_w = min(natural_label_w, max(22.0, ind_right - x))
                L.text(painter, QRectF(x, y, lbl_w, row_h), label, look.c(look.text), px, 600,
                       Qt.AlignLeft | Qt.AlignVCenter)
                ind_left = x + lbl_w + 5
                if ind_right - ind_left >= 10:
                    bar_h = max(3.5, round(px * 0.36))
                    L.bar(painter, QRectF(ind_left, y + (row_h - bar_h) / 2, ind_right - ind_left, bar_h),
                          share, ai.health_color(left, look.dark), track_col)

    def _paint_single_window(self, painter: QPainter, area: QRectF, look: L.Look,
                             label: str, window: dict) -> None:
        """Draw a single limit window with a circular ring (default) or horizontal bar."""
        show_used = bool(self.dock.cfg.get('ai_show_used', False))
        pct, share, left = ai.shown_value(window, show_used=show_used)
        share = self.eased(('ai', label), share)
        reset = ai.format_reset(window.get('resets_at'))
        if self._tile_style() != 'bars':
            ring_s = min(area.height() * 0.68, area.width() * 0.32)
            ry = area.center().y() - ring_s / 2.0
            L.ring(painter, QRectF(area.x(), ry, ring_s, ring_s), share,
                   ai.health_color(left, look.dark), look.c(look.track), max(2.6, ring_s * 0.12))
            self.lines(painter, QRectF(area.x() + ring_s + 6, area.y(),
                                       max(20.0, area.width() - ring_s - 6), area.height()),
                       f'{pct}%', reset or label, roll=('ai', label))
            return
        big, small = self.sizes()
        top_h = small * 1.2
        top_y = area.center().y() - (top_h + 9) / 2
        L.text(painter, QRectF(area.x(), top_y, area.width() * 0.58, top_h), f'{label} · {pct}%',
               look.c(look.text), small, 600, Qt.AlignLeft | Qt.AlignVCenter)
        L.text(painter, QRectF(area.x() + area.width() * 0.55, top_y, area.width() * 0.45, top_h), reset,
               look.c(look.sub), small, 400, Qt.AlignRight | Qt.AlignVCenter)
        bar_h = max(4.0, round(big * 0.3))
        L.bar(painter, QRectF(area.x(), top_y + top_h + 4, area.width(), bar_h),
              share, ai.health_color(left, look.dark), look.c(look.track))

    def _subtitle_text(self, snap: dict[str, dict], providers: tuple[str, ...] = ai.PROVIDERS) -> str:
        mode = 'Allowance used' if self.dock.cfg.get('ai_show_used', False) else 'Allowance remaining'
        if not self.dock.testing and self.dock.ai.is_busy:
            return f'{mode} · Updating…'
        live_tag = ' · Live' if ai.normalize_refresh_mode(self.dock.cfg.get('ai_refresh')) == 'live' else ''
        stamps = [snap[p]['updated_at'] for p in providers
                  if snap.get(p, {}).get('connected') and snap[p].get('updated_at')]
        if stamps:
            age = ai.format_age(max(stamps))
            if age:
                return f'{mode}{live_tag} · {age}'
        return f'{mode}{live_tag}'

    def refresh_now(self) -> None:
        self.dock.ai.fetch(force=True)

    def set_show_used(self, show_used: bool) -> None:
        self.dock.cfg['ai_show_used'] = bool(show_used)
        self.dock.changed('ai')
        self._sync_open_card()

    def set_refresh_mode(self, refresh_mode: str) -> None:
        self.dock.cfg['ai_refresh'] = ai.normalize_refresh_mode(refresh_mode)
        self.dock.ai.configure()
        self.dock.changed('ai')
        for t in self.dock.tiles:
            if isinstance(t, _AITile):
                t._sync_open_card()

    def menu(self, menu, pos):
        menu.addAction('Refresh limits', self.refresh_now)
        if ai.normalize_refresh_mode(self.dock.cfg.get('ai_refresh')) != 'live':
            menu.addAction('Live updates', lambda: self.set_refresh_mode('live'))
        show_used = bool(self.dock.cfg.get('ai_show_used', False))
        menu.addAction('Show remaining %' if show_used else 'Show used %',
                       lambda: self.set_show_used(not show_used))
        menu.addSeparator()

    def _populate_provider_rows(self, providers: tuple[str, ...]) -> None:
        if self._providers_box is None:
            return
        while self._providers_box.count():
            item = self._providers_box.takeAt(0)
            old_w = item.widget()
            if old_w is not None:
                old_w.hide()
                old_w.setParent(None)
                old_w.deleteLater()
        self._card_rows = []
        snap = self.snapshot()
        self._card_shape = self._shape_for(snap, providers)
        show_used = bool(self.dock.cfg.get('ai_show_used', False))
        clock24 = bool(self.dock.cfg.get('clock24', False))
        ratio = self.devicePixelRatioF() or 2.0

        for provider in providers:
            info = ai.INFO[provider]
            state = snap.get(provider) or {}
            block = QWidget()
            block_lay = QVBoxLayout(block)
            block_lay.setContentsMargins(0, 0, 0, 0)
            block_lay.setSpacing(6)

            head = QHBoxLayout()
            head.setSpacing(8)
            badge = QLabel()
            badge.setPixmap(ai.provider_pixmap(provider, 18, self.look.dark, ratio))
            badge.setFixedSize(18, 18)
            head.addWidget(badge)
            plan = f" · {state['plan']}" if state.get('plan') else ''
            title_lbl = _label(f"{info['name']}{plan}")
            title_lbl.setStyleSheet('font-weight: 600;')
            head.addWidget(title_lbl, 1)
            block_lay.addLayout(head)

            if state.get('connected') and state.get('windows'):
                grid = QGridLayout()
                grid.setContentsMargins(0, 2, 0, 2)
                grid.setHorizontalSpacing(8)
                grid.setVerticalSpacing(8)
                for col in range(4):
                    grid.setColumnStretch(col, 1)
                    grid.setColumnMinimumWidth(col, 74)
                for idx, win_data in enumerate(state['windows']):
                    ring_cell = _UsageRingCell(self.look, win_data, show_used, clock24)
                    self._card_rows.append((provider, idx, ring_cell))
                    grid.addWidget(ring_cell, idx // 4, idx % 4)
                block_lay.addLayout(grid)
                for line in _cost_lines(state):              # usage credits and the API price estimate
                    block_lay.addWidget(_label(line, 'sub'))
                if state.get('error'):                       # the last reading is kept: why it is not newer
                    block_lay.addWidget(_label(state['error'], 'sub'))
            else:
                note = state.get('error') or info['hint']
                block_lay.addWidget(_label(note, 'sub'))

            self._providers_box.addWidget(block)
            block.show()

        self._providers_box.invalidate()
        self._providers_box.activate()
        host = self._providers_box.parentWidget()
        if host is not None:
            host.adjustSize()
        if self._mode_sub is not None:
            self._mode_sub.setText(self._subtitle_text(snap, providers))

    def _open_ai_card(self, providers: tuple[str, ...], multi_service: bool = False) -> None:
        self._card_multi = multi_service
        active = tuple(self.enabled_providers()) if multi_service else providers
        snap = self.snapshot()
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(_label(self.NAME, 'title'), 1)
        refresh_btn = _button(L.G['refresh'], self.refresh_now, 'icon')
        refresh_btn.setToolTip('Refresh now')
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        self._mode_sub = _label(self._subtitle_text(snap, active), 'sub')
        layout.addWidget(self._mode_sub)

        providers_host = QWidget()
        self._providers_box = QVBoxLayout(providers_host)
        self._providers_box.setContentsMargins(0, 0, 0, 0)
        self._providers_box.setSpacing(10)
        layout.addWidget(providers_host)
        self._populate_provider_rows(active)

        # Collapsible Settings dropdown — hidden until clicked so the card stays clean and compact
        toggle_btn = _button('Settings  ▾', name='row')
        layout.addWidget(toggle_btn)

        drawer = QWidget()
        drawer.setVisible(False)
        drawer_lay = QVBoxLayout(drawer)
        drawer_lay.setContentsMargins(2, 2, 2, 0)
        drawer_lay.setSpacing(8)
        layout.addWidget(drawer)

        def toggle_drawer():
            open_now = drawer.isHidden()
            drawer.setVisible(open_now)
            toggle_btn.setText('Settings  ▴' if open_now else 'Settings  ▾')
            if self.card is not None:
                self.card.refit()

        toggle_btn.clicked.connect(toggle_drawer)

        show_used = bool(self.dock.cfg.get('ai_show_used', False))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_row.addWidget(_label('Display', 'sub'))
        mode_group = QButtonGroup(drawer)
        mode_group.setExclusive(True)
        for flag, word in ((False, 'Remaining'), (True, 'Used')):
            btn = _button(word, lambda _=False, f=flag: self.set_show_used(f), checkable=True)
            btn.setChecked(show_used == flag)
            mode_group.addButton(btn)
            mode_row.addWidget(btn)
        mode_row.addStretch(1)
        drawer_lay.addLayout(mode_row)

        style_row = QHBoxLayout()
        style_row.setSpacing(6)
        style_row.addWidget(_label('Widget', 'sub'))
        style_group = QButtonGroup(drawer)
        style_group.setExclusive(True)
        cur_style = self._tile_style()

        def set_style(kind: str):
            self.dock.cfg['ai_tile_style'] = kind
            for t in self.dock.tiles:
                if isinstance(t, _AITile) and t.ID not in (self.dock.cfg.get('widget_widths') or {}):
                    t.setFixedWidth(round(self.dock.tile_height * t.default_units()))
            self.dock.changed('ai')
            self.dock.fit_now()
            if self.card is not None:
                self.card.refit()

        for kind, word in (('rings', 'Rings'), ('gauges', 'Gauges'), ('bars', 'Bars')):
            btn = _button(word, lambda _=False, k=kind: set_style(k), checkable=True)
            btn.setChecked(cur_style == kind)
            style_group.addButton(btn)
            style_row.addWidget(btn)
        style_row.addStretch(1)
        drawer_lay.addLayout(style_row)

        refresh_row = QHBoxLayout()
        refresh_row.setSpacing(6)
        refresh_row.addWidget(_label('Updates', 'sub'))
        refresh_group = QButtonGroup(drawer)
        refresh_group.setExclusive(True)
        cur_refresh = ai.normalize_refresh_mode(self.dock.cfg.get('ai_refresh'))
        for mode_key, word in (('live', 'Live'), ('30s', '30s'), ('1m', '1m'), ('5m', '5m')):
            btn = _button(word, lambda _=False, m=mode_key: self.set_refresh_mode(m), checkable=True)
            btn.setChecked(cur_refresh == mode_key)
            refresh_group.addButton(btn)
            refresh_row.addWidget(btn)
        refresh_row.addStretch(1)
        drawer_lay.addLayout(refresh_row)

        if multi_service:
            # Show/hide AI services completely from both the card and the bar tile
            svc_row = QHBoxLayout()
            svc_row.setSpacing(6)
            svc_row.addWidget(_label('Services', 'sub'))
            svc_buttons: dict[str, QPushButton] = {}

            pair_holder = QWidget()
            pair_row = QHBoxLayout(pair_holder)
            pair_row.setContentsMargins(0, 0, 0, 0)
            pair_row.setSpacing(6)
            pair_row.addWidget(_label('On bar', 'sub'))
            pair_buttons: dict[str, QPushButton] = {}

            def sync_pair_visibility():
                enabled_now = self.enabled_providers()
                pair_holder.setVisible(len(enabled_now) > 2)
                pair_now = [p for p in (self.dock.cfg.get('ai_pair') or []) if p in enabled_now]
                for extra in enabled_now:
                    if len(pair_now) >= 2:
                        break
                    if extra not in pair_now:
                        pair_now.append(extra)
                for key, b in pair_buttons.items():
                    b.setVisible(key in enabled_now)
                    b.blockSignals(True)
                    b.setChecked(key in pair_now[:2])
                    b.blockSignals(False)

            def toggle_service(picked: str):
                enabled_now = self.enabled_providers()
                if picked in enabled_now:
                    if len(enabled_now) <= 1:          # keep at least one AI service enabled
                        svc_buttons[picked].blockSignals(True)
                        svc_buttons[picked].setChecked(True)
                        svc_buttons[picked].blockSignals(False)
                        return
                    enabled_now = [p for p in enabled_now if p != picked]
                else:
                    enabled_now = [p for p in ai.PROVIDERS if p in enabled_now or p == picked]
                self.dock.cfg['ai_enabled'] = enabled_now
                # Keep preferred bar pair aligned with enabled services
                pair_now = [p for p in (self.dock.cfg.get('ai_pair') or []) if p in enabled_now]
                for extra in enabled_now:
                    if len(pair_now) >= 2:
                        break
                    if extra not in pair_now:
                        pair_now.append(extra)
                self.dock.cfg['ai_pair'] = pair_now[:2]
                for key, b in svc_buttons.items():
                    b.blockSignals(True)
                    b.setChecked(key in enabled_now)
                    b.blockSignals(False)
                sync_pair_visibility()
                self._populate_provider_rows(tuple(enabled_now))
                self.dock.changed('ai')
                if not self.dock.testing:
                    self.dock.ai.fetch()
                if self.card is not None:
                    self.card.refit()

            for p in ai.PROVIDERS:
                btn = _button(ai.INFO[p]['short'], lambda _=False, key=p: toggle_service(key), checkable=True)
                btn.setChecked(p in active)
                svc_buttons[p] = btn
                svc_row.addWidget(btn)
            svc_row.addStretch(1)
            drawer_lay.addLayout(svc_row)

            def choose_bar_pair(picked: str):
                enabled_now = self.enabled_providers()
                chosen = [p for p in (self.dock.cfg.get('ai_pair') or []) if p in enabled_now]
                if picked in chosen and len(chosen) > 1:
                    for extra in enabled_now:
                        if extra not in chosen:
                            chosen = [p for p in chosen if p != picked] + [extra]
                            break
                elif picked not in chosen:
                    chosen = [chosen[-1], picked] if chosen else [picked]
                self.dock.cfg['ai_pair'] = chosen[:2]
                sync_pair_visibility()
                self.dock.changed('ai')

            for p in ai.PROVIDERS:
                btn = _button(ai.INFO[p]['short'], lambda _=False, key=p: choose_bar_pair(key), checkable=True)
                pair_buttons[p] = btn
                pair_row.addWidget(btn)
            pair_row.addStretch(1)
            drawer_lay.addWidget(pair_holder)
            sync_pair_visibility()

        warn_row = QHBoxLayout()
        warn_row.setSpacing(6)
        warn_row.addWidget(_label('Low alert', 'sub'))
        warn_group = QButtonGroup(drawer)
        warn_group.setExclusive(True)
        warn_on = bool(self.dock.cfg.get('ai_warn_low', True))

        def set_warn(flag: bool):
            self.dock.cfg['ai_warn_low'] = bool(flag)
            self.dock.cfg.save()

        for flag, word in ((True, 'Below 15%'), (False, 'Off')):
            btn = _button(word, lambda _=False, f=flag: set_warn(f), checkable=True)
            btn.setChecked(warn_on == flag)
            warn_group.addButton(btn)
            warn_row.addWidget(btn)
        warn_row.addStretch(1)
        drawer_lay.addLayout(warn_row)

        self.open_card(content, 376)
        if not self.dock.testing:
            self.dock.ai.poke(providers=active, max_age=15.0)


class AILimitsTile(_AITile):
    ID, NAME = 'ai_limits', 'AI Limits'
    ABOUT = 'Remaining allowance and reset times for two AI plans: Claude, ChatGPT or Gemini.'
    WIDTH = 4.3

    def chosen_rows(self, snap: dict[str, dict]) -> list[tuple[str | None, str, dict]]:
        enabled = self.enabled_providers()
        connected = [p for p in enabled if snap.get(p, {}).get('connected') and snap[p].get('windows')]
        preferred = [p for p in (self.dock.cfg.get('ai_pair') or ['claude', 'chatgpt']) if p in connected]
        ordered = preferred + [p for p in connected if p not in preferred]
        if len(ordered) >= 2:
            p1, p2 = ordered[0], ordered[1]
            return [
                (p1, ai.INFO[p1]['short'], snap[p1]['windows'][0]),
                (p2, ai.INFO[p2]['short'], snap[p2]['windows'][0]),
            ]
        if len(ordered) == 1:
            only = ordered[0]
            wins = snap[only]['windows']
            if len(wins) >= 2:
                return [(only, wins[0]['label'], wins[0]), (only, wins[1]['label'], wins[1])]
            return [(only, ai.INFO[only]['short'], wins[0])]
        return []

    def paint(self, painter, rect, look):
        snap = self.snapshot()
        rows = self.chosen_rows(snap)
        area = rect.adjusted(7, 0, -7, 0)
        if len(rows) >= 2:
            self._paint_rows(painter, area, look, rows)
        elif len(rows) == 1:
            provider, label, window = rows[0]
            side = rect.height()
            badge_s = side * 0.64
            ai.paint_provider_mark(painter, QRectF(rect.x() + 7, rect.center().y() - badge_s / 2, badge_s, badge_s),
                                   provider or 'claude', look.dark)
            self._paint_single_window(painter, QRectF(rect.x() + badge_s + 12, rect.y(),
                                                      rect.width() - badge_s - 19, side), label, window)
        else:
            enabled = self.enabled_providers()
            first = enabled[0] if enabled else 'claude'
            side = rect.height()
            badge_s = side * 0.62
            ai.paint_provider_mark(painter, QRectF(rect.x() + 7, rect.center().y() - badge_s / 2, badge_s, badge_s),
                                   first, look.dark)
            self.lines(painter, QRectF(rect.x() + badge_s + 13, rect.y(), rect.width() - badge_s - 20, side),
                       'AI Limits', 'Click to connect')

    def click(self, pos):
        self._open_ai_card(tuple(self.enabled_providers()), multi_service=True)


class _SingleProviderAITile(_AITile):
    WIDTH = 4.55

    def paint(self, painter, rect, look):
        snap = self.snapshot()
        state = snap.get(self.PROVIDER) or {}
        side = rect.height()
        badge_s = side * 0.66
        ai.paint_provider_mark(painter, QRectF(rect.x() + 7, rect.center().y() - badge_s / 2, badge_s, badge_s),
                               self.PROVIDER, look.dark)
        right_area = QRectF(rect.x() + badge_s + 12, rect.y(), rect.width() - badge_s - 19, side)
        windows = state.get('windows') or []
        if state.get('connected') and len(windows) >= 2:
            rows = [(None, windows[0]['label'], windows[0]), (None, windows[1]['label'], windows[1])]
            self._paint_rows(painter, right_area, look, rows)
        elif state.get('connected') and len(windows) == 1:
            self._paint_single_window(painter, right_area, look, windows[0]['label'], windows[0])
        else:
            status = 'Session expired' if 'expired' in (state.get('error') or '').lower() else 'Sign in'
            self.lines(painter, right_area, ai.INFO[self.PROVIDER]['short'], status)

    def click(self, pos):
        self._open_ai_card((self.PROVIDER,), multi_service=False)


class ClaudeTile(_SingleProviderAITile):
    ID, NAME, PROVIDER = 'claude', 'Claude Usage', 'claude'
    ABOUT = 'Your Claude session and weekly allowance left, and when each window resets.'


class ChatGPTTile(_SingleProviderAITile):
    ID, NAME, PROVIDER = 'chatgpt', 'ChatGPT Usage', 'chatgpt'
    ABOUT = "Your ChatGPT plan's 5-hour and weekly Codex allowance left, and when each resets."


class GeminiTile(_SingleProviderAITile):
    ID, NAME, PROVIDER = 'gemini', 'Gemini Usage', 'gemini'
    ABOUT = 'Your Gemini session and weekly quota left, and when each window resets.'


WIDGETS: list[type[Tile]] = [
    StartTile, TaskViewTile, AppsTile, SearchTile, RecycleTile, StorageTile, ScreenshotTile,
    AILimitsTile, ClaudeTile, ChatGPTTile, GeminiTile,
    ProcessorTile, MemoryTile, NetworkTile, BatteryTile, TrayTile, LanguageTile, QuickSettingsTile, NotificationsTile, ShowDesktopTile, KeyboardTile,
    ThemeTile, DayProgressTile,
    MediaTile, VolumeTile,
    TimerTile, StopwatchTile, PomodoroTile, NotesTile,
    ClockTile,
    WeatherTile,
]
REGISTRY = {widget.ID: widget for widget in WIDGETS}
GROUPS = ['Apps & Files', 'AI', 'System', 'Media & Audio', 'Productivity', 'Clocks', 'Weather']
