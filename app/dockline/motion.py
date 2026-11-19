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
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
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

GAUGE_MS = 240         # a gauge gliding to a new reading (the suite's GENTLE)
ROLL_MS = 180          # figures rolling to a new value (the suite's STEADY)
HOP_MS = 560           # a started app's icon hopping in its tile
HOP = 0.13             # how high, as a share of the tile
LAUNCH_MOST = 15.0     # seconds an app is shown as starting, at most
BREATH = 1.2           # seconds a starting app's dot takes to dim and come back
PULSE = 0.9            # seconds of one pulse of an app asking for attention
PULSES = 3             # pulses before it holds, as the taskbar's flashing does
PULSE_EVERY = 33       # ms between frames of pulses alone: they need no more
LIFT = 1.12            # a dragged icon, picked up
TEAR = 1.0             # tile heights past the dock at which a dragged pinned icon comes off


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


def hop(t: float) -> float:
    """A started app's icon, 0 resting to 1 at the top, at t (0 to 1) of HOP_MS: a hop, then a small one."""
    if t <= 0.0 or t >= 1.0:
        return 0.0
    if t < 0.62:
        return math.sin(math.pi * t / 0.62)
    return 0.28 * math.sin(math.pi * (t - 0.62) / 0.38)


def breath(seconds: float) -> float:
    """A starting app's dot: full, down to a quarter and back, every BREATH seconds."""
    return 0.25 + 0.75 * (math.cos(seconds / BREATH * 2 * math.pi) + 1) / 2


def pulse(seconds: float) -> float:
    """An app asking for attention: PULSES pulses from nothing to full, then full."""
    if seconds >= PULSE * PULSES:
        return 1.0
    return (1 - math.cos(max(0.0, seconds) / PULSE * 2 * math.pi)) / 2


class Glides:
    """Values a widget paints, each gliding to its newest reading instead of jumping there: call it
    from paintEvent with a key and the reading, and paint what it returns. The first reading shows
    as it is."""

    def __init__(self, widget: QWidget, ms: int = GAUGE_MS):
        self.widget = widget
        self.ms = ms
        self.items: dict = {}

    def __call__(self, key, target: float) -> float:
        item = self.items.get(key)
        if item is None:
            self.items[key] = {'shown': target, 'from': target, 'to': target, 'anim': None}
            return target
        if abs(item['to'] - target) < 1e-9:
            return item['shown']
        item['from'], item['to'] = item['shown'], target
        anim = item['anim']
        if not on() or not self.widget.isVisible():
            if anim is not None:
                anim.stop()
            item['shown'] = target
            return target
        if anim is None:
            anim = item['anim'] = QVariantAnimation(self.widget)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setDuration(self.ms)
            anim.setEasingCurve(curve())

            def step(value, item=item):
                item['shown'] = item['from'] + (item['to'] - item['from']) * float(value)
                self.widget.update()

            anim.valueChanged.connect(step)
        anim.stop()
        anim.start()
        return item['shown']

    def forget(self, keep) -> None:
        """Drop the values whose keys are no longer painted."""
        for key in [key for key in self.items if key not in keep]:
            anim = self.items.pop(key)['anim']
            if anim is not None:
                anim.stop()
                anim.deleteLater()


def _number(text: str) -> int:
    """The figures in a reading, as one number: 37% is 37, 04:59 is 459 (so a countdown rolls down)."""
    digits = ''.join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


class Roll:
    """Figures that roll to a new value, as the suite's Rolling does on Hanami's edge panel: each
    character that changes slides out as its new one slides in, up as the value rises, down as it
    falls. Drawn with the dock's own text (its halo on glass included), in any alignment."""

    def __init__(self, widget: QWidget):
        self.widget = widget
        self.text = ''
        self.old = ''
        self.t = 1.0
        self.up = True
        self.anim: QVariantAnimation | None = None

    def set(self, text: str) -> None:
        if text == self.text:
            return
        self.old, self.text = self.text, text
        self.up = _number(text) >= _number(self.old)
        if self.anim is not None:
            self.anim.stop()
        if not on() or not self.widget.isVisible() or not self.old:
            self.t = 1.0
            return
        if self.anim is None:
            self.anim = QVariantAnimation(self.widget)
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.setDuration(ROLL_MS)
            self.anim.setEasingCurve(QEasingCurve(QEasingCurve.OutCubic))
            self.anim.valueChanged.connect(lambda value: (setattr(self, 't', float(value)), self.widget.update()))
        self.t = 0.0
        self.anim.start()

    def paint(self, painter: QPainter, rect: QRectF, text: str, color: QColor, pixels: float, weight: int = 600,
              align=Qt.AlignLeft) -> None:
        """text in rect (centred in its height), rolling from the last text painted."""
        self.set(text)
        font = L.font(pixels, weight)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        rolling = self.t < 1.0 and self.old != self.text
        count = max(len(self.old), len(self.text)) if rolling else len(self.text)
        new = self.text.rjust(count)
        old = self.old.rjust(count) if rolling else new
        width = metrics.horizontalAdvance(self.text)
        if align & Qt.AlignRight:
            right = rect.right()
        elif align & Qt.AlignHCenter:
            right = rect.center().x() + width / 2
        else:
            right = rect.x() + width
        travel = max(rect.height(), metrics.height()) * 0.8
        away = -1 if self.up else 1                    # rising: the old one goes up, the new comes from below
        painter.save()
        painter.setClipRect(QRectF(rect.x() - rect.width(), rect.y(), rect.width() * 3, rect.height()),
                            Qt.IntersectClip)
        flags = Qt.AlignRight | Qt.AlignVCenter
        x = right
        for a, b in zip(reversed(old), reversed(new)):
            step = max(metrics.horizontalAdvance(a), metrics.horizontalAdvance(b))
            x -= step
            slot = QRectF(x, rect.y(), step, rect.height())
            if rolling and a != b:
                parts = ((a, away * travel * self.t, 1 - self.t), (b, -away * travel * (1 - self.t), self.t))
            else:
                parts = ((b, 0.0, 1.0),)
            for char, shift, alpha in parts:
                if not char.strip():
                    continue
                painter.setOpacity(alpha)
                place = slot.translated(0, shift)
                L._halo(painter, place, flags, char)
                painter.setPen(color)
                painter.drawText(place, flags, char)
        painter.setOpacity(1.0)
        painter.restore()


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


def layout(count: int, slot: float, gap: float, pointer: float | None, strength: float,
           weights=None) -> list[tuple[float, float]]:
    """Each app's left edge and width, in the strip's coordinates: wider apps push the row outward
    around the pointer, so the app under it stays under it. weights are the apps' presence, 0 to 1,
    for apps coming or going: they take that share of their room."""
    sizes = scales(count, pointer, strength)
    weights = [1.0] * count if weights is None else list(weights)
    widths = [slot * s * w for s, w in zip(sizes, weights)]
    lefts, x = [], 0.0
    for width, weight in zip(widths, weights):
        lefts.append(x)
        x += width + gap * weight
    total = x - gap if count else 0.0
    plain = sum((slot + gap) * w for w in weights) - gap if count else 0.0
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
        if area != self.geometry():
            self.setGeometry(area)

    def step(self) -> None:
        # Kept over the strip every frame: placed only as it first showed, it stayed where the dock
        # was then - mid-slide up from hiding, or before the dock widened for a new app - and the
        # grown icons ran past its top edge, cut off.
        if self.isVisible():
            self.place()
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

    def placed(self) -> list:
        """Each app shown, those coming or going too, with its tile in the strip's coordinates."""
        strip = self.strip
        slot = strip.dock.tile_height
        shown = ease(self.strength)
        entries = strip.display()
        rows = layout(len(entries), strip.slot, strip.GAP, self.pointer, shown, [e.presence for e in entries])
        edge_top = strip.dock.edge == 'top'
        rest = min(float(slot), strip.slot)
        top = (strip.height() - rest) / 2              # where the tiles rest: they grow away from the edge
        out = []
        for entry, (left, width) in zip(entries, rows):
            y = top if edge_top else top + rest - width
            out.append((entry, QRectF(left + strip.offset(), y, width, width)))
        return out

    def rects(self) -> list[QRectF]:
        """Each open or pinned app's tile, in the strip's coordinates."""
        return [rect for entry, rect in self.placed() if entry.index >= 0]

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)     # what was drawn before goes:
        painter.fillRect(self.rect(), Qt.transparent)                  # glass drawn over it showed it through
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        origin = self.strip.mapToGlobal(self.strip.rect().topLeft()) - self.mapToGlobal(self.rect().topLeft())
        painter.translate(origin)
        rest = self.strip.slot
        hovered = None
        for entry, rect in self.placed():
            # Above the bar there is no glass under a tile: it brings its own, more of it the more it
            # has grown, so at rest it looks just as it does in the bar.
            plain = rest * max(entry.presence, 0.01)
            grown = max(0.0, min(1.0, (rect.width() / plain - 1) / (MAGNIFY - 1) * 1.6))
            if grown > 0.01:
                backing = self.strip.dock.glass_fill()          # the dock's own glass, as clear
                backing.setAlpha(round(max(backing.alpha(), 40) * grown * entry.presence))
                radius = rect.width() * 0.2
                painter.setPen(Qt.NoPen)
                painter.setBrush(backing)
                painter.drawRoundedRect(rect, radius, radius)
            self.strip.paint_entry(painter, entry, rect)
            if entry.index >= 0 and entry.index == self.strip.hover_index:
                hovered = (entry, rect)
        if hovered is not None and self.strength > 0.5 and not self.strip.previews_visible():
            entry, rect = hovered
            self.paint_label(painter, rect, self.strip.name_of(entry.index), min(1.0, (self.strength - 0.5) * 2))

    def paint_label(self, painter: QPainter, tile: QRectF, name: str, alpha: float) -> None:
        paint_pill(painter, self.strip, tile, name, alpha)


def paint_pill(painter: QPainter, strip, tile: QRectF, name: str, alpha: float = 1.0) -> None:
    """An app's name (or what letting it go will do) in a small pill above it, as the original's
    preview shows; below it on a dock along the top."""
    if not name:
        return
    look = strip.look
    font = L.font(12, 600)
    painter.setFont(font)
    width = painter.fontMetrics().horizontalAdvance(name) + 20
    height = 24
    top = tile.top() - height - 8 if strip.dock.edge != 'top' else tile.bottom() + 8
    pill = QRectF(tile.center().x() - width / 2, top, width, height)
    painter.setOpacity(alpha)
    fill = strip.dock.panel_fill()
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
        self.picture = picture
        self.origin = origin
        self.toward = toward
        self.progress = 0.0
        self.setGeometry(where)

    alive: list = []        # held from Python until Qt has deleted them (as closed cards: see card.py)

    def start(self) -> None:
        import shiboken6
        Ghost.alive = [ghost for ghost in Ghost.alive if shiboken6.isValid(ghost)] + [self]
        self.show()
        # hidden, then deleted by Qt's event loop, only after the animation has finished with it
        run(self, CLOSE_MS, self.step, lambda: QTimer.singleShot(0, self.finish), QEasingCurve(QEasingCurve.OutCubic))

    def finish(self) -> None:
        self.hide()
        self.deleteLater()

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


# ---------------------------------------------------------------- a dragged app

class Lift(QWidget):
    """An app's icon picked up off the dock, over everything: a little larger, with a shadow,
    following the pointer. Pulled away from the dock, a pinned app says it will be unpinned; let go
    there, it fades away; let go in the row, it settles into its place."""

    PAD = 14            # room for the shadow
    LABEL_H = 24

    def __init__(self, strip):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.strip = strip
        self.entry = None
        self.label = ''
        self.scale = LIFT
        self.opacity = 1.0
        self.centre = QPointF()
        self.anim = None

    def side(self) -> float:
        return self.strip.dock.tile_height * LIFT

    def lift(self, entry, centre: QPointF) -> None:
        self.stop()
        self.entry = entry
        self.label = ''
        self.opacity = 1.0
        self.scale = 1.0
        self.carry(centre)
        self.show()
        self.anim = run(self, HOVER_MS, lambda p: self.set_scale(1.0 + (LIFT - 1.0) * p),
                        easing=QEasingCurve(QEasingCurve.OutCubic))

    def set_scale(self, scale: float) -> None:
        self.scale = scale
        self.update()

    def carry(self, centre: QPointF, label: str = '') -> None:
        """The tile's centre at centre (global), with label (Unpin) over it or none."""
        self.centre = QPointF(centre)
        if label != self.label:
            self.label = label
            self.update()
        side = self.side() + 2 * self.PAD
        font = L.font(12, 600)
        from PySide6.QtGui import QFontMetrics
        width = max(side, QFontMetrics(font).horizontalAdvance(label) + 28 if label else 0)
        height = side + self.LABEL_H + 8
        top_edge = self.strip.dock.edge == 'top'
        y = centre.y() - side / 2 - (0 if top_edge else self.LABEL_H + 8)
        self.setGeometry(round(centre.x() - width / 2), round(y), round(width), round(height))

    def tile_rect(self) -> QRectF:
        """Where the tile is drawn, in this window."""
        side = self.strip.dock.tile_height * self.scale
        here = self.centre - QPointF(self.geometry().topLeft())
        return QRectF(here.x() - side / 2, here.y() - side / 2, side, side)

    def stop(self) -> None:
        if self.anim is not None:
            try:
                self.anim.stop()
            except RuntimeError:
                pass
        self.anim = None

    def land(self, centre: QPointF, then) -> None:
        """Into the row: glide to its place there, shrinking back to its size, then hand over."""
        self.stop()
        start, scale = QPointF(self.centre), self.scale
        self.label = ''

        def step(progress):
            self.carry(start + (centre - start) * progress)
            self.set_scale(scale + (1.0 - scale) * progress)

        def done():
            self.anim = None
            self.hide()
            then()

        self.anim = run(self, SLIDE_MS, step, done)

    def vanish(self, then=None) -> None:
        """Let go away from the dock: shrinks and fades where it is."""
        self.stop()
        scale = self.scale
        self.label = ''

        def step(progress):
            self.opacity = 1.0 - progress
            self.set_scale(scale * (1.0 - 0.4 * progress))

        def done():
            self.anim = None
            self.hide()
            if then:
                then()

        self.anim = run(self, CLOSE_MS, step, done, QEasingCurve(QEasingCurve.InCubic))

    def paintEvent(self, _):
        if self.entry is None:
            return
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setOpacity(self.opacity)
        rect = self.tile_rect()
        radius = rect.width() * 0.2
        lifted = max(0.0, min(1.0, (self.scale - 1.0) / (LIFT - 1.0)))
        painter.setPen(Qt.NoPen)
        for ring in range(6):                            # a soft shadow, more of it the higher it is
            grow = 2 + ring * 2
            painter.setBrush(QColor(0, 0, 0, round(14 * lifted)))
            painter.drawRoundedRect(rect.adjusted(-grow / 2, -grow / 2 + 4, grow / 2, grow / 2 + 4),
                                    radius + grow / 2, radius + grow / 2)
        backing = self.strip.dock.glass_fill()           # off the dock: glass of its own under it
        backing.setAlpha(max(backing.alpha(), 150))
        painter.setBrush(backing)
        painter.drawRoundedRect(rect, radius, radius)
        self.strip.paint_entry(painter, self.entry, rect, lifted=True)
        if self.label:
            paint_pill(painter, self.strip, rect, self.label, self.opacity)
