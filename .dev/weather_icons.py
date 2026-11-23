"""Draws the dock's weather icons at the bar's size and the card's, light and dark, beside the icon
font's own sun and cloud: .dev\\out\\weather_icons.png."""
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QRectF                 # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication        # noqa: E402

from dockline import look as L                    # noqa: E402

app = QApplication([])
kinds = ['sun', 'cloud', 'rain', 'snow', 'storm']
cell = 56
image = QImage(cell * len(kinds) * 2, cell * 4, QImage.Format_ARGB32)
image.fill(QColor(0, 0, 0, 0))
p = QPainter(image)
for row, (dark, pixels) in enumerate(((True, 18.7), (True, 16), (False, 18.7), (False, 16))):
    for col, kind in enumerate(kinds):
        for scale in (1, 2):
            x = (col * 2 + scale - 1) * cell
            p.fillRect(x, row * cell, cell, cell, QColor(40, 40, 46) if dark else QColor(236, 236, 240))
            L.glyph(p, QRectF(x, row * cell, cell, cell), kind,
                    QColor(240, 240, 244) if dark else QColor(24, 24, 27), pixels * scale)
p.end()
out = Path(__file__).with_name('out')
out.mkdir(exist_ok=True)
image.save(str(out / 'weather_icons.png'))
print('saved')
