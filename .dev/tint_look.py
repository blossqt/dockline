"""Draws a dark card's glass over a stand-in screen (dark above, a white page below), with the even
tint as before and with readable_tint, side by side: .dev\\out\\tint_look.png."""
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QRectF, Qt                    # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap   # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from dockline import look as L                           # noqa: E402

app = QApplication([])
W, H = 380, 320
screen = QImage(W, H, QImage.Format_RGB32)
screen.fill(QColor(18, 18, 22))
p = QPainter(screen)
p.fillRect(0, 170, W, H - 170, QColor(250, 250, 250))
p.setPen(QColor(30, 30, 30))
p.setFont(QFont('Segoe UI', 16))
p.drawText(20, 210, 'Activity Details')
p.setFont(QFont('Segoe UI', 10))
for i in range(4):
    p.drawText(30, 240 + i * 18, 'Task: View this topic and more text behind')
p.end()
backdrop = L._frost(screen, 1.0)
fill = QColor(0, 0, 0, 89)

sheet = QPixmap(2 * W + 20, H)
sheet.fill(QColor(60, 60, 60))
out = QPainter(sheet)
for n, tint in enumerate((None, L.readable_tint(backdrop, fill, True))):
    left = n * (W + 20)
    out.drawImage(left, 0, screen)
    pane = QRectF(left + 30, 20, W - 60, H - 40)
    out.setRenderHint(QPainter.Antialiasing)
    L.paint_backdrop(out, pane, 13, backdrop.copy(30, 20, W - 60, H - 40))
    part = QRectF(30 / L.TINT_CELL, 20 / L.TINT_CELL, (W - 60) / L.TINT_CELL, (H - 40) / L.TINT_CELL)
    L.paint_clear_glass(out, pane, 13, fill, True, tint, part)
    out.setPen(QColor(240, 240, 244))
    out.setFont(QFont('Segoe UI', 12, QFont.DemiBold))
    out.drawText(QRectF(pane.x() + 16, pane.y() + 14, 300, 24), 'AI Limits')
    out.setFont(QFont('Segoe UI', 10))
    for i, line in enumerate(['Allowance used · Live', 'Session 0%   Weekly 81%', '', '',
                              'The Claude Code sign-in has expired.', 'Run claude auth login to sign in',
                              'again.', 'Settings']):
        out.drawText(QRectF(pane.x() + 16, pane.y() + 48 + i * 26, 300, 22), line)
out.end()
Path(__file__).with_name('out').mkdir(exist_ok=True)
sheet.save(str(Path(__file__).with_name('out') / 'tint_look.png'))
print('saved')
