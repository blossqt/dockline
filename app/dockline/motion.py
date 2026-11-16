"""How the dock moves: one curve and timing for everything, the apps' magnification, and cards
growing out of their widget and back.

The curve and timings follow the original's dock: cubic-bezier(0.32, 0.72, 0, 1), 220 ms to come
in, 180 ms to go, 200 ms for a hover to light up, and a press that darkens at once. The
magnification follows its launch video: the app under the pointer grows to about 1.85 times its
size and its neighbours less with distance, rising out of the bar, the row widening around the
pointer so the app under it stays there.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from . import look as L
from . import suitetheme as theme

OPEN_MS = 220          # a card or the dock coming in
CLOSE_MS = 180         # going away
HOVER_MS = 200         # a hover lighting up
SLIDE_MS = 220         # widgets making room for one being dragged
PRESS_DARK = 0.3       # a pressed tile darkens to 70% brightness

MAGNIFY = 1.85         # the app under the pointer, at full magnification
REACH = 2.6            # how many apps either side grow with it
FOLLOW = 0.45          # how far the magnified row moves toward the pointer each frame (of 60 a second)


def curve() -> QEasingCurve:
    """The original's easing: quick out, long gentle landing."""
    easing = QEasingCurve(QEasingCurve.BezierSpline)
    easing.addCubicBezierSegment(QPointF(0.32, 0.72), QPointF(0.0, 1.0), QPointF(1.0, 1.0))
    return easing


_curve = None


def ease(t: float) -> float:
    global _curve
    if _curve is None:
        _curve = curve()
    return _curve.valueForProgress(max(0.0, min(1.0, t)))


def on() -> bool:
    """The suite's Animations setting (Hanami), and Windows' own."""
    try:
        return theme.motion_on()
    except Exception:
        return True


def run(owner, ms: int, step, done=None, easing: QEasingCurve | None = None) -> QVariantAnimation | None:
    """Call step(progress 0..1, eased) once a frame for ms, then done(). Without animations, at once."""
    if not on() or ms <= 0:
        step(1.0)
        if done:
            done()
        return None
    animation = QVariantAnimation(owner)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setDuration(ms)
    animation.setEasingCurve(easing or curve())
    animation.valueChanged.connect(lambda value: step(float(value)))
    if done:
        animation.finished.connect(done)
    animation.start(QVariantAnimation.DeleteWhenStopped)
    return animation


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t), round(a.alpha() + (b.alpha() - a.alpha()) * t))


# ---------------------------------------------------------------- magnification

def scales(count: int, pointer: float | None, strength: float) -> list[float]:
    """Each app's scale, for a pointer at a position in slots (0 = the first app's left edge)."""
    if pointer is None or strength <= 0:
        return [1.0] * count
    out = []
    for index in range(count):
        distance = abs(index + 0.5 - pointer)
        if distance >= REACH:
            out.append(1.0)
        else:
            bump = (math.cos(distance / REACH * math.pi) + 1) / 2          # 1 under the pointer, 0 at REACH
            out.append(1.0 + (MAGNIFY - 1.0) * bump * strength)
    return out


def layout(count: int, slot: float, gap: float, pointer: float | None, strength: float) -> list[tuple[float, float]]:
    """Each app's left edge and width, in the strip's coordinates: wider apps push the row outward
    around the pointer, so the app under it stays under it."""
    sizes = scales(count, pointer, strength)
    widths = [slot * s for s in sizes]
    lefts, x = [], 0.0
    for width in widths:
        lefts.append(x)
        x += width + gap
    total = x - gap if count else 0.0
    plain = count * slot + max(0, count - 1) * gap
    share = 0.5 if pointer is None else max(0.0, min(1.0, pointer / max(count, 1)))
    shift = (total - plain) * share
    return [(left - shift, width) for left, width in zip(lefts, widths)]


class Magnifier(QWidget):
    """The apps while magnified: a see-through layer over the apps strip and above it, which takes
    no clicks (the strip under it does), drawn while the pointer is over the apps or they settle."""

    RISE = 0.9          # room above the bar, in apps' heights

    def __init__(self, strip):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.strip = strip
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.pointer: float | None = None       # where the row is centred, in slots (eased toward target)
        self.target: float | None = None
        self.strength = 0.0
        self.want = 0.0
        self.last = time.perf_counter()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.step)

    @property
    def active(self) -> bool:
        return self.isVisible() and (self.strength > 0.001 or self.want > 0)

    def margin(self) -> tuple[int, int]:
        slot = self.strip.dock.tile_height
        return round(slot * (MAGNIFY - 1) * REACH), round(slot * self.RISE) + 30

    def follow(self, pointer_slots: float | None) -> None:
        """The pointer moved over the strip (in slots), or left it (None)."""
        if pointer_slots is None:
            self.want = 0.0
        else:
            self.want = 1.0
            self.target = pointer_slots
            if self.pointer is None or self.strength < 0.02:
                self.pointer = pointer_slots
        if not on():
            self.strength, self.pointer = self.want, self.target
        if not self.timer.isActive():
            self.last = time.perf_counter()
            self.timer.start(max(4, theme.frame_interval()))
        if self.want and not self.isVisible():
            self.place()
            self.show()
        self.step()

    def drop(self) -> None:
        """Stop magnifying at once (an icon is being dragged)."""
        self.want = self.strength = 0.0
        self.target = self.pointer = None
        self.timer.stop()
        self.hide()
        self.strip.magnifier_done()

    def place(self) -> None:
        side, up = self.margin()
        top_left = self.strip.mapToGlobal(self.strip.rect().topLeft())
        area = QRect(top_left.x() - side, top_left.y() - up, self.strip.width() + 2 * side,
                     self.strip.height() + up + 4)
        if self.strip.dock.edge == 'top':
            area.moveTop(top_left.y() - 4)
        self.setGeometry(area)

    def step(self) -> None:
        now = time.perf_counter()
        frames = min(4.0, (now - self.last) * 60)
        self.last = now
        rate = 1 - (1 - FOLLOW) ** frames
        if self.target is not None and self.pointer is not None:
            self.pointer += (self.target - self.pointer) * rate
        # grow in HOVER_MS and shrink back in CLOSE_MS, eased
        speed = frames / 60 * 1000 / (HOVER_MS if self.want > self.strength else CLOSE_MS)
        if self.want > self.strength:
            self.strength = min(self.want, self.strength + speed)
        else:
            self.strength = max(self.want, self.strength - speed)
        settled = self.strength == self.want and (self.target is None or self.pointer is None or
                                                  abs(self.target - self.pointer) < 0.002)
        if settled and self.want == 0:
            self.timer.stop()
            self.hide()
            self.strip.magnifier_done()
            return
        if settled:
            self.timer.stop()
        self.update()
        self.strip.update()

    def rects(self) -> list[QRectF]:
        """Each app's tile, in the strip's coordinates."""
        strip = self.strip
        slot = strip.dock.tile_height
        shown = ease(self.strength)
        rows = layout(len(strip.groups), strip.slot, strip.GAP, self.pointer, shown)
        edge_top = strip.dock.edge == 'top'
        rest = min(float(slot), strip.slot)
        top = (strip.height() - rest) / 2              # where the tiles rest: they grow away from the edge
        out = []
        for left, width in rows:
            y = top if edge_top else top + rest - width
            out.append(QRectF(left + strip.offset(), y, width, width))
        return out

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)     # what was drawn before goes:
        painter.fillRect(self.rect(), Qt.transparent)                  # glass drawn over it showed it through
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        origin = self.strip.mapToGlobal(self.strip.rect().topLeft()) - self.mapToGlobal(self.rect().topLeft())
        painter.translate(origin)
        rects = self.rects()
        rest = self.strip.slot
        look = self.strip.look
        for index, rect in enumerate(rects):
            # Above the bar there is no glass under a tile: it brings its own, more of it the more it
            # has grown, so at rest it looks just as it does in the bar.
            grown = max(0.0, min(1.0, (rect.width() / rest - 1) / (MAGNIFY - 1) * 1.6))
            if grown > 0.01:
                backing = self.strip.dock.glass_fill()          # the dock's own glass, as clear
                backing.setAlpha(round(max(backing.alpha(), 40) * grown))
                radius = rect.width() * 0.2
                painter.setPen(Qt.NoPen)
                painter.setBrush(backing)
                painter.drawRoundedRect(rect, radius, radius)
            self.strip.paint_app(painter, index, rect)
        index = self.strip.hover_index
        if 0 <= index < len(rects) and self.strength > 0.5 and not self.strip.previews_visible():
            self.paint_label(painter, rects[index], self.strip.name_of(index), min(1.0, (self.strength - 0.5) * 2))

    def paint_label(self, painter: QPainter, tile: QRectF, name: str, alpha: float) -> None:
        """The app's name in a small pill above it, as the original's preview shows."""
        if not name:
            return
        look = self.strip.look
        font = L.font(12, 600)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(name) + 20
        height = 24
        top = tile.top() - height - 8 if self.strip.dock.edge != 'top' else tile.bottom() + 8
        pill = QRectF(tile.center().x() - width / 2, top, width, height)
        painter.setOpacity(alpha)
        fill = self.strip.dock.panel_fill()
        if look.glass:
            fill.setAlpha(max(fill.alpha(), 150))        # a short label over anything: kept readable
        painter.setPen(QPen(look.c(look.edge), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(pill, height / 2, height / 2)
        painter.setPen(look.c(look.text))
        painter.drawText(pill, Qt.AlignCenter, name)
        painter.setOpacity(1.0)


# ---------------------------------------------------------------- cards

def card_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def scaled(rect: QRectF, origin: QPointF, factor: float, lift: float) -> QRectF:
    """rect grown or shrunk about origin, moved lift pixels toward it."""
    return QRectF(origin.x() + (rect.x() - origin.x()) * factor,
                  origin.y() + (rect.y() - origin.y()) * factor + lift,
                  rect.width() * factor, rect.height() * factor)


class Ghost(QWidget):
    """A closing card's picture, shrinking back toward its widget and fading, after the card itself
    has gone (a pop-up closes at once on a click elsewhere)."""

    def __init__(self, picture: QPixmap, where: QRect, origin: QPointF, toward: float):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.picture = picture
        self.origin = origin
        self.toward = toward
        self.progress = 0.0
        self.setGeometry(where)

    def start(self) -> None:
        self.show()
        # closed (and so deleted) only after the animation has finished with it
        run(self, CLOSE_MS, self.step, lambda: QTimer.singleShot(0, self.close), QEasingCurve(QEasingCurve.OutCubic))

    def step(self, progress: float) -> None:
        self.progress = progress
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)     # what was drawn before goes:
        painter.fillRect(self.rect(), Qt.transparent)                  # glass drawn over it showed it through
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setOpacity(1 - self.progress)
        target = scaled(QRectF(self.rect()), self.origin, 1 - 0.08 * self.progress, self.toward * self.progress)
        painter.drawPixmap(target, self.picture, QRectF(self.picture.rect()))
