"""Colours, fonts, the icon and small drawing helpers shared by the dock, its cards and windows."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QPainter,
                           QPainterPath, QPen, QPixmap)

# Neutral colours (the person's wish): white on dark and near-black on light for what is chosen,
# rings and bars. The suite's pink stays in the app's icon only.
ICON_PINK = '#ffd1dc'
ICON_ROSE = '#c2255c'
LIGHT_INK = '#f4f4f5'
DARK_INK = '#18181b'
ICON_CORNER = 42 / 192

SIZES = {'S': 44, 'M': 52, 'L': 60}     # dock height in CSS-like pixels
MARGIN = 6                              # space between the dock and the screen's edges
GAP = 6                                 # space between tiles and inside the bar
TEXT = {'S': (13, 12), 'M': (15, 12), 'L': (17, 13)}   # main and small text, pixels


@dataclass(frozen=True)
class Look:
    name: str
    label: str
    dark: bool
    glass: bool
    bar: tuple
    edge: tuple
    tile: tuple
    hover: tuple
    text: str
    sub: tuple
    accent: str
    track: tuple
    panel: tuple

    def c(self, value) -> QColor:
        return QColor(value) if isinstance(value, str) else QColor(*value)


LOOKS = {
    'black': Look('black', 'Black', True, False, (10, 10, 10, 242), (255, 255, 255, 26), (255, 255, 255, 14),
                  (255, 255, 255, 34), '#fafafa', (255, 255, 255, 140), LIGHT_INK, (255, 255, 255, 40),
                  (24, 24, 27, 250)),
    'white': Look('white', 'White', False, False, (250, 250, 250, 242), (0, 0, 0, 26), (0, 0, 0, 10),
                  (0, 0, 0, 24), '#0a0a0a', (99, 99, 107, 255), DARK_INK, (0, 0, 0, 30), (250, 250, 250, 252)),
    'glass': Look('glass', 'Glass', True, True, (24, 24, 30, 70), (255, 255, 255, 40), (255, 255, 255, 0),
                  (255, 255, 255, 30), '#ffffff', (255, 255, 255, 160), LIGHT_INK, (255, 255, 255, 50),
                  (28, 28, 34, 246)),
    # the glass in light mode (Windows' or Hanami's): dark text on pale glass
    'glass_light': Look('glass_light', 'Glass', False, True, (255, 255, 255, 90), (0, 0, 0, 30), (0, 0, 0, 0),
                        (0, 0, 0, 20), '#0a0a0a', (60, 60, 68, 255), DARK_INK, (0, 0, 0, 34), (250, 250, 252, 246)),
    'midnight': Look('midnight', 'Midnight', True, False, (11, 26, 51, 242), (255, 255, 255, 28),
                     (255, 255, 255, 16), (255, 255, 255, 36), '#ffffff', (255, 255, 255, 140), LIGHT_INK,
                     (255, 255, 255, 44), (16, 33, 62, 250)),
}

STYLES = ['black', 'white', 'glass', 'midnight']    # the choices in Settings

_icon_family = None


def icon_family() -> str:
    """Windows 11's icon font, or Windows 10's, which has the same code points for these."""
    global _icon_family
    if _icon_family is None:
        families = set(QFontDatabase.families())
        _icon_family = 'Segoe Fluent Icons' if 'Segoe Fluent Icons' in families else 'Segoe MDL2 Assets'
    return _icon_family


def font(pixels: float, weight: int = 400) -> QFont:
    result = QFont('Segoe UI Variable Text')
    result.setPixelSize(max(1, round(pixels)))
    result.setWeight(QFont.Weight(weight))
    try:
        result.setFeature(QFont.Tag.fromString('tnum'), 1)      # figures of one width
    except (AttributeError, TypeError):
        pass
    return result


def rgba(value) -> str:
    color = QColor(value) if isinstance(value, (str, QColor)) else QColor(*value)
    return f'rgba({color.red()},{color.green()},{color.blue()},{color.alpha()})'


# Fluent icon code points
G = {
    'search': '', 'bin': '', 'volume': '', 'mute': '', 'mic': '',
    'prev': '', 'next': '', 'play': '', 'pause': '', 'stopwatch': '',
    'timer': '', 'note': '', 'camera': '', 'drive': '', 'sun': '',
    'moon': '', 'cloud': '', 'desktop': '', 'bell': '', 'settings': '',
    'down': '', 'up': '', 'left': '', 'right': '', 'toggles': '',
    'rain': '', 'snow': '', 'storm': '', 'focus': '', 'calendar': '',
}
G['chevron_up'] = ''           # the tray's "show hidden icons"
G['refresh'] = '\ue72c'         # refresh AI limits


def chip(look) -> QColor:
    """What is chosen or highlighted: white on a dark look, near-black on a light one."""
    return QColor(LIGHT_INK if look.dark else DARK_INK)


def on_chip(look) -> QColor:
    return QColor('#111114' if look.dark else '#fafafa')


# Over clear glass, text and icons get a soft halo of the opposite tone, so they read over any
# wallpaper or window behind. Set by the dock for the Glass style (None: no halo).
HALO: QColor | None = None


def _halo(painter: QPainter, rect: QRectF, flags, value: str) -> None:
    if HALO is None:
        return
    pen = painter.pen()
    painter.setPen(HALO)
    painter.drawText(rect.translated(0, 1), flags, value)
    painter.setPen(pen)


def glyph(painter: QPainter, rect: QRectF, name: str, color: QColor, pixels: float) -> None:
    f = QFont(icon_family())
    f.setPixelSize(max(1, round(pixels)))
    painter.setFont(f)
    _halo(painter, rect, Qt.AlignCenter, G.get(name, name))
    painter.setPen(color)
    painter.drawText(rect, Qt.AlignCenter, G.get(name, name))


def text(painter: QPainter, rect: QRectF, value: str, color: QColor, pixels: float, weight: int = 400,
         align=Qt.AlignCenter) -> None:
    painter.setFont(font(pixels, weight))
    metrics = painter.fontMetrics()
    shown = metrics.elidedText(value, Qt.ElideRight, int(rect.width()))
    _halo(painter, rect, align, shown)
    painter.setPen(color)
    painter.drawText(rect, align, shown)


def ring(painter: QPainter, rect: QRectF, share: float, color: QColor, track: QColor, width: float) -> None:
    inset = rect.adjusted(width / 2, width / 2, -width / 2, -width / 2)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(track, width, Qt.SolidLine, Qt.RoundCap))
    painter.drawEllipse(inset)
    if share > 0.002:
        painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(inset, 90 * 16, -round(min(1.0, share) * 360 * 16))


def bar(painter: QPainter, rect: QRectF, share: float, color: QColor, track: QColor) -> None:
    radius = rect.height() / 2
    painter.setPen(Qt.NoPen)
    painter.setBrush(track)
    painter.drawRoundedRect(rect, radius, radius)
    if share > 0:
        filled = QRectF(rect.x(), rect.y(), max(rect.height(), rect.width() * min(1.0, share)), rect.height())
        painter.setBrush(color)
        painter.drawRoundedRect(filled, radius, radius)


def windows_logo(painter: QPainter, rect: QRectF, color: QColor) -> None:
    side = min(rect.width(), rect.height())
    gap = side * 0.08
    cell = (side - gap) / 2
    left = rect.center().x() - side / 2
    top = rect.center().y() - side / 2
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    for row in range(2):
        for column in range(2):
            painter.drawRoundedRect(QRectF(left + column * (cell + gap), top + row * (cell + gap), cell, cell),
                                    side * 0.06, side * 0.06)


def play_pause(painter: QPainter, rect: QRectF, color: QColor) -> None:
    """A combined play and pause mark: the dock cannot tell which state the player is in."""
    side = min(rect.width(), rect.height())
    x = rect.center().x() - side / 2
    y = rect.center().y() - side / 2
    path = QPainterPath()
    path.moveTo(QPointF(x, y + side * 0.1))
    path.lineTo(QPointF(x + side * 0.5, y + side * 0.5))
    path.lineTo(QPointF(x, y + side * 0.9))
    path.closeSubpath()
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawPath(path)
    for offset in (0.62, 0.84):
        painter.drawRoundedRect(QRectF(x + side * offset, y + side * 0.12, side * 0.13, side * 0.76),
                                side * 0.05, side * 0.05)


# ---------------------------------------------------------------- the app's icon

def render_app_icon(size: int) -> QPixmap:
    """The suite's tile: pink, corners at 42/192, a deep-rose mark of a dock holding three tiles."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 64.0, size / 64.0)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(ICON_PINK))
    corner = 64 * ICON_CORNER
    painter.drawRoundedRect(QRectF(0, 0, 64, 64), corner, corner)
    painter.setBrush(QColor(ICON_ROSE))
    if size <= 24:                                   # bolder at small sizes
        painter.drawRoundedRect(QRectF(13, 38, 38, 8), 4, 4)
        for i in range(3):
            painter.drawRoundedRect(QRectF(15 + i * 12, 22, 10, 11), 2.5, 2.5)
    else:
        painter.drawRoundedRect(QRectF(14, 39, 36, 7), 3.5, 3.5)
        for i in range(3):
            painter.drawRoundedRect(QRectF(16 + i * 11.5, 24, 9, 10), 2.5, 2.5)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 256):
        icon.addPixmap(render_app_icon(size))
    return icon


# ---------------------------------------------------------------- style sheets

def paint_clear_glass(painter: QPainter, rect: QRectF, radius: float, fill: QColor, dark: bool) -> None:
    """The Glass style's pane, for everything that opens from the dock: the suite's frosted glass - an
    even tint over what is behind, blurred (paint_backdrop), and a hairline round its edge, as the
    suite's pop-ups have (suitetheme._paint_frosted). No rim of light."""
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, fill)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 22), 1))
    painter.drawPath(path)


def frosted_behind(where) -> QPixmap | None:
    """The screen behind a pop-up about to show (global rect), blurred: what the Glass style's cards
    and menus are drawn over. Windows' own blur can't be used for them on this PC's Windows: the
    acrylic accent comes out flat dark grey, and the system backdrop flat light grey for a window
    that is not the active one. Taken before the pop-up is on screen, so it is never in it."""
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.screenAt(where.center()) or QGuiApplication.primaryScreen()
    if screen is None or where.width() <= 0 or where.height() <= 0:
        return None
    area = screen.geometry()
    shot = screen.grabWindow(0, where.x() - area.x(), where.y() - area.y(), where.width(), where.height())
    if shot.isNull():
        return None
    return _frost(shot.toImage(), shot.devicePixelRatio())


def _frost(image, ratio: float) -> QPixmap:
    size = image.size()
    small = image.scaled(max(1, size.width() // 4), max(1, size.height() // 4), Qt.IgnoreAspectRatio,
                         Qt.SmoothTransformation)
    small = small.scaled(max(1, size.width() // 14), max(1, size.height() // 14), Qt.IgnoreAspectRatio,
                         Qt.SmoothTransformation)
    blurred = QPixmap.fromImage(small.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    blurred.setDevicePixelRatio(ratio)
    return blurred


def frosted_band(where, keep_out=None) -> QPixmap | None:
    """frosted_behind for a band across the screen (global rect), for a pop-up that moves along it
    (the previews, sliding from app to app): taken once, before the pop-up is on screen. The rows
    keep_out covers (a layer of the dock's own over part of the band: the magnified icons and their
    name) are left out, and the rows beside them carried over them, so the blur never holds them."""
    from PySide6.QtGui import QGuiApplication, QImage
    screen = QGuiApplication.screenAt(where.center()) or QGuiApplication.primaryScreen()
    if screen is None or where.width() <= 0 or where.height() <= 0:
        return None
    top, bottom = where.top(), where.top() + where.height()          # rows taken: [top, bottom)
    if keep_out is not None and keep_out.isValid() and keep_out.top() < bottom and keep_out.bottom() + 1 > top:
        if keep_out.top() > top:
            bottom = keep_out.top()                                  # it covers the band's foot
        else:
            top = keep_out.bottom() + 1                              # or its head
        if bottom - top < 4:
            return None
    area = screen.geometry()
    shot = screen.grabWindow(0, where.x() - area.x(), top - area.y(), where.width(), bottom - top)
    if shot.isNull():
        return None
    ratio = shot.devicePixelRatio()
    taken = shot.toImage()
    if (top, bottom) == (where.top(), where.top() + where.height()):
        return _frost(taken, ratio)
    whole = QImage(round(where.width() * ratio), round(where.height() * ratio), QImage.Format_ARGB32_Premultiplied)
    whole.fill(Qt.transparent)
    painter = QPainter(whole)
    offset = round((top - where.top()) * ratio)
    painter.drawImage(0, offset, taken)
    edge_row = 0 if offset > 0 else taken.height() - 1               # the row next to what was left out
    edge = taken.copy(0, edge_row, taken.width(), 1)
    if offset > 0:
        painter.drawImage(QRectF(0, 0, whole.width(), offset), edge)
    else:
        rest = taken.height()
        painter.drawImage(QRectF(0, rest, whole.width(), whole.height() - rest), edge)
    painter.end()
    return _frost(whole, ratio)


def paint_backdrop(painter: QPainter, rect: QRectF, radius: float, backdrop) -> None:
    """A frosted backdrop (frosted_behind) inside a pane's rounded shape."""
    if backdrop is None or backdrop.isNull():
        return
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setClipPath(path)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.drawPixmap(rect, backdrop, QRectF(backdrop.rect()))
    painter.restore()


def card_css(look: Look) -> str:
    fg = look.text
    PINK, ON_PINK = chip(look).name(), on_chip(look).name()
    return f"""
    QWidget {{ color: {fg}; font-family: 'Segoe UI Variable Text'; font-size: 14px; }}
    QLabel {{ background: transparent; }}
    QLabel#title {{ font-size: 16px; font-weight: 600; }}
    QLabel#big {{ font-size: 30px; font-weight: 600; }}
    QLabel#sub {{ color: {rgba(look.sub)}; font-size: 13px; }}
    QPushButton {{ background: {rgba(look.tile)}; border: none; border-radius: 8px; padding: 6px 12px;
                   min-height: 20px; }}
    QPushButton:hover {{ background: {rgba(look.hover)}; }}
    QPushButton:checked, QPushButton#primary {{ background: {PINK}; color: {ON_PINK}; }}
    QPushButton#icon {{ font-family: '{icon_family()}'; font-size: 14px; padding: 6px; min-width: 20px; }}
    QPushButton#row {{ text-align: left; padding: 8px 10px; }}
    QLineEdit, QPlainTextEdit {{ background: {rgba(look.tile)}; border: 1px solid {rgba(look.edge)};
                   border-radius: 8px; padding: 6px 8px; selection-background-color: {PINK};
                   selection-color: {ON_PINK}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {rgba(look.track)}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {look.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {fg}; width: 14px; height: 14px; margin: -5px 0;
                   border-radius: 7px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ width: 6px; background: transparent; }}
    QScrollBar::handle:vertical {{ background: {rgba(look.track)}; border-radius: 3px; min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """


# Settings (and the widget picker) in the dock's style. Each: window ground, page ground, text, quieter
# text, fields, lines, hover, panels (ground, line), buttons.
WINDOW_STYLES = {
    'black': ('#111113', '#111113', '#fafafa', '#9a9aa3', '#1c1c20', '#2c2c32', '#26262b', ('#18181b', '#2a2a30'),
              '#1c1c20'),
    'white': ('#f7f7f8', '#f7f7f8', '#0a0a0a', '#63636b', '#ffffff', '#e2e2e6', '#ececef', ('#ffffff', '#e4e4e8'),
              '#ffffff'),
    'midnight': ('#0b1a33', '#0b1a33', '#ffffff', 'rgba(255,255,255,150)', '#12264a', '#22406e', '#1a3360',
                 ('#10223f', '#22406e'), '#12264a'),
}


def settings_css(look: 'Look', opacity: int = 18) -> str:
    """The Settings window in the dock's style: Black, White and Midnight in their solid colours;
    Glass see-through to Windows' blur behind the window, tinted as the dock is (Dock.glass_percent), with
    its panels on firmer ground so what is read stays readable."""
    if look.glass:
        share = max(0, min(100, int(opacity))) / 100
        if look.dark:
            tint = f'rgba(16,16,22,{round(255 * share)})'
            styles = (tint, 'transparent', '#ffffff', 'rgba(255,255,255,170)', 'rgba(255,255,255,18)',
                      'rgba(255,255,255,40)', 'rgba(255,255,255,30)',
                      (f'rgba(16,16,22,{min(255, max(150, round(255 * share) + 60))})', 'rgba(255,255,255,30)'),
                      'rgba(255,255,255,16)')
        else:
            tint = f'rgba(255,255,255,{round(255 * share)})'
            styles = (tint, 'transparent', '#0a0a0a', '#4a4a52', 'rgba(255,255,255,150)', 'rgba(0,0,0,28)',
                      'rgba(0,0,0,16)', (f'rgba(255,255,255,{min(255, max(185, round(255 * share) + 60))})', 'rgba(0,0,0,22)'),
                      'rgba(255,255,255,130)')
    else:
        styles = WINDOW_STYLES.get(look.name, WINDOW_STYLES['black' if look.dark else 'white'])
    root, page, fg, sub, field, line, hover, panel, button = styles
    PINK, ON_PINK = chip(look).name(), on_chip(look).name()
    window = root
    return f"""
    QWidget#window {{ background: {window}; }}
    QWidget#root, QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget {{ background: {page}; }}
    QWidget {{ color: {fg}; font-family: 'Segoe UI Variable Text'; font-size: 14px; }}
    QLabel, QCheckBox {{ background: transparent; }}
    QLabel#heading {{ font-size: 13px; font-weight: 600; color: {sub}; }}
    QLabel#title {{ font-size: 15px; font-weight: 600; }}
    QLabel#name {{ font-weight: 600; }}
    QLabel#sub {{ color: {sub}; font-size: 13px; }}
    QPushButton {{ background: {button}; border: 1px solid {line}; border-radius: 8px; padding: 7px 14px; }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:checked {{ background: {PINK}; color: {ON_PINK}; border-color: {PINK}; }}
    QLineEdit {{ background: {button}; border: 1px solid {line}; border-radius: 8px; padding: 7px 10px;
                 selection-background-color: {PINK}; selection-color: {ON_PINK}; }}
    QCheckBox {{ spacing: 10px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {line};
                 background: {button}; }}
    QCheckBox::indicator:checked {{ background: {PINK}; border-color: {PINK}; image: none; }}
    QListWidget {{ background: {panel[0]}; border: 1px solid {panel[1]}; border-radius: 12px; outline: none;
                   padding: 6px; }}
    QListWidget::item {{ padding: 8px 10px; border-radius: 8px; color: {fg}; }}
    QListWidget::item:selected {{ background: {PINK}; color: {ON_PINK}; }}
    QListWidget::item:hover:!selected {{ background: {hover}; }}
    QScrollArea {{ border: none; }}
    QSlider::groove:horizontal {{ height: 4px; background: {line}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {PINK}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {fg}; width: 16px; height: 16px; margin: -6px 0;
                 border-radius: 8px; }}
    QFrame#section {{ background: {panel[0]}; border: 1px solid {panel[1]}; border-radius: 12px; }}
    QFrame#choice {{ background: {panel[0]}; border: 1px solid {panel[1]}; border-radius: 12px; }}
    QFrame#choice:hover {{ border-color: {sub}; }}
    QFrame#choice[on="true"] {{ border-color: {fg}; }}
    QScrollBar:vertical {{ width: 8px; background: transparent; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {line}; border-radius: 3px; min-height: 32px; }}
    QScrollBar::handle:vertical:hover {{ background: {sub}; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        height: 0; background: transparent; }}
    QScrollBar:horizontal {{ height: 0; }}
    """
