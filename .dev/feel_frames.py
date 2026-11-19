"""Pictures of Dockline 0.5.10's new motion, mid-move, drawn without showing a window (grab() on the
normal platform, real fonts): an app growing in, a started app's hop and breathing dot, attention,
the bar under the app in front widening, a rolling CPU figure, and a pinned icon pulled off the dock.

    python .dev\\feel_frames.py [out.png]"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ['APPDATA'] = tempfile.mkdtemp(prefix='dockline-frames-')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dockline import motion, win  # noqa: E402
from dockline.config import Config  # noqa: E402
from dockline.dock import Dock  # noqa: E402
from dockline.widgets import Shown  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), 'dockline_feel.png'))
app = QApplication([])
exe = sys.executable
windows = [win.Window(101, 'Explorer', 1, r'C:\Windows\explorer.exe'),
           win.Window(102, 'Notepad', 2, r'C:\Windows\System32\notepad.exe'),
           win.Window(103, 'Python', 3, exe)]
win.app_windows = lambda pid: list(windows)
win.foreground = lambda: 101


def make(look: str):
    cfg = Config(None)
    cfg['widgets'] = ['start', 'apps', 'cpu', 'clock']
    cfg['look'] = look
    cfg['pinned'] = [r'C:\Windows\System32\mspaint.exe']
    dock = Dock(cfg, testing=True)
    dock.resize(dock.row.sizeHint().width(), dock.bar_height)
    dock.layout().activate()
    strip = next(t for t in dock.tiles if t.ID == 'apps')
    strip.refresh()
    dock.resize(dock.row.sizeHint().width(), dock.bar_height)
    dock.layout().activate()
    return dock, strip


def backdrop(width, height):
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0, QColor('#5b6f8a'))
    gradient.setColorAt(1, QColor('#c9a3b8'))
    painter.fillRect(image.rect(), gradient)
    painter.end()
    return image


frames = []


def shoot(label, dock, extra=None):
    picture = dock.grab().toImage()
    frames.append((label, picture, extra))


for look in ('glass', 'light'):
    dock, strip = make(look)
    keys = list(strip.keys)
    shoot(f'{look}: at rest (Explorer in front)', dock)
    # an app growing in, halfway
    strip.presence = {keys[-1]: 0.5}
    dock.resize(dock.row.sizeHint().width(), dock.bar_height)
    dock.layout().activate()
    shoot(f'{look}: Python growing in, halfway', dock)
    strip.presence = {}
    dock.resize(dock.row.sizeHint().width(), dock.bar_height)
    dock.layout().activate()
    # a pinned app started: near the top of its hop, its dot breathing
    paint_key = next(k for k, g in zip(strip.keys, strip.groups) if g.pinned)
    now = time.perf_counter()
    strip.hops = {paint_key: now - motion.HOP_MS / 1000 * 0.31}
    strip.launches = {paint_key: {'at': now - 0.1, 'count': 0, 'known': set()}}
    shoot(f'{look}: Paint started, mid-hop', dock)
    strip.hops = {}
    strip.launches = {paint_key: {'at': now - motion.BREATH / 2, 'count': 0, 'known': set()}}
    shoot(f'{look}: Paint starting, dot at its dimmest', dock)
    strip.launches = {}
    # attention, at a pulse's height
    strip.flashing = {102}
    strip.flash_at = {102: time.perf_counter() - motion.PULSE / 2}
    shoot(f'{look}: Notepad asking for attention', dock)
    strip.flashing, strip.flash_at = set(), {}
    # the bar under the app in front, mid-glide to Notepad
    strip.front = 102
    for key in keys:
        strip.bars.items[key] = {'shown': 0.5 if key in (keys[0], keys[1]) else 0.0, 'from': 0, 'to': 0, 'anim': None}
    shoot(f'{look}: front moving from Explorer to Notepad', dock)
    strip.front = 101
    strip.bars.items.clear()
    # a rolling CPU figure
    cpu = next(t for t in dock.tiles if t.ID == 'cpu')
    roll = cpu.roll('ring')
    roll.old, roll.text, roll.t, roll.up = '37%', '42%', 0.45, True
    cpu.share = 0.42
    cpu.paint = (lambda painter, rect, look_, tile=cpu, r=roll:
                 (tile.lines(painter, QRectF(rect.x() + rect.height() - 2, rect.y(), rect.width() - rect.height() - 4,
                                             rect.height()), '42%', 'CPU', roll='ring')))
    shoot(f'{look}: CPU 37% rolling to 42%', dock)
    # a pinned icon pulled off the dock
    lift = motion.Lift(strip)
    index = strip.keys.index(paint_key)
    lift.entry = Shown(paint_key, strip.groups[index], index, 1.0)
    lift.scale = motion.LIFT
    lift.carry(QPointF(400, 300), 'Unpin')
    shoot(f'{look}: Paint pulled off the dock', dock, lift.grab().toImage())
    lift.deleteLater()

zoom = 3
strip_x = strip.mapTo(dock, strip.rect().topLeft()).x() - 6
strip_w = strip.width() + 12
rows = []
for label, picture, extra in frames:
    crop = picture.copy(strip_x, 0, strip_w, picture.height())
    big = crop.scaled(crop.width() * zoom // round(crop.devicePixelRatio() or 1), crop.height() * zoom
                      // round(crop.devicePixelRatio() or 1), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    rows.append((label, big, extra))
width = max(max(b.width(), 0) + (e.width() + 20 if e is not None else 0) for _, b, e in rows) + 270
heights = [max(b.height(), e.height() if e is not None else 0) + 12 for _, b, e in rows]
sheet = backdrop(width, sum(heights) + 20)
painter = QPainter(sheet)
painter.setFont(QFont('Segoe UI', 9))
y = 10
for (label, big, extra), h in zip(rows, heights):
    painter.setPen(QColor('white'))
    painter.drawText(QRectF(10, y, 240, h - 12), Qt.AlignVCenter | Qt.TextWordWrap, label)
    painter.drawImage(250, y, big)
    if extra is not None:
        painter.drawImage(250 + big.width() + 20, y, extra)
    y += h
painter.end()
sheet.save(str(out))
print(out)
