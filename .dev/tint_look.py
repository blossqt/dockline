"""Draws a card's glass over stand-in screens: a dark card over a dark top and a white page, and a
light card over a dark navy page, each with the even tint (left) and readable_tint (right):
.dev\\out\\tint_look.png. With --old D L M, also the targets READ_DARK, READ_LIGHT, READ_MOST given."""
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QRectF                         # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap   # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from dockline import look as L                           # noqa: E402

app = QApplication([])
W, H = 380, 300


def screen(dark_card: bool) -> QImage:
    image = QImage(W, H, QImage.Format_RGB32)
    p = QPainter(image)
    if dark_card:
        image.fill(QColor(18, 18, 22))
        p.fillRect(0, 160, W, H - 160, QColor(250, 250, 250))
        p.setPen(QColor(30, 30, 30))
    else:
        image.fill(QColor(22, 22, 52))
        p.fillRect(0, 220, W, H - 220, QColor(250, 250, 250))
        p.setPen(QColor(230, 230, 240))
    p.setFont(QFont('Segoe UI', 12))
    for i in range(6):
        p.drawText(30, 40 + i * 36, 'Compare budget in-ear monitors and more')
    p.end()
    return image


def card(out, left, top, image, dark, tint_on):
    backdrop = L._frost(image, 1.0)
    fill = QColor(0, 0, 0, 89) if dark else QColor(255, 255, 255, 81)
    tint = L.readable_tint(backdrop, fill, dark) if tint_on else None
    out.drawImage(left, top, image)
    pane = QRectF(left + 30, top + 20, W - 60, H - 40)
    out.setRenderHint(QPainter.Antialiasing)
    L.paint_backdrop(out, pane, 13, backdrop.copy(30, 20, W - 60, H - 40))
    part = QRectF(30 / L.TINT_CELL, 20 / L.TINT_CELL, (W - 60) / L.TINT_CELL, (H - 40) / L.TINT_CELL)
    L.paint_clear_glass(out, pane, 13, fill, dark, tint, part)
    out.setPen(QColor(240, 240, 244) if dark else QColor(24, 24, 27))
    out.setFont(QFont('Segoe UI', 12, QFont.DemiBold))
    out.drawText(QRectF(pane.x() + 16, pane.y() + 14, 300, 24), 'AI Limits')
    out.setFont(QFont('Segoe UI', 10))
    for i, line in enumerate(['Allowance used · Live · Updated 10s ago', 'Claude · Pro', 'Session 25%   Weekly 83%',
                              '4h 38m   Sun, Nov 22', '', '', 'Settings']):
        out.drawText(QRectF(pane.x() + 16, pane.y() + 48 + i * 26, 320, 22), line)


sheet = QPixmap(2 * W + 20, 2 * H + 20)
sheet.fill(QColor(60, 60, 60))
out = QPainter(sheet)
for row, dark in enumerate((True, False)):
    for col in (0, 1):
        card(out, col * (W + 20), row * (H + 20), screen(dark), dark, col == 1)
out.end()
Path(__file__).with_name('out').mkdir(exist_ok=True)
sheet.save(str(Path(__file__).with_name('out') / 'tint_look.png'))
print('saved')
