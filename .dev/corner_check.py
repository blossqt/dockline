"""Two bars of the suite's frosted glass on screen for a moment, as Dockline's bar is drawn: one as
0.5.19-0.5.25 had it (Windows rounding the window too), one with glassfix keeping its own corners.
Their ends are pictured 8x into .dev\\out\\corners_*.png. Nothing is clicked; no window takes focus.
    python .dev\\corner_check.py"""
import ctypes
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

from PySide6.QtCore import QRectF, Qt, QTimer      # noqa: E402
from PySide6.QtGui import QGuiApplication, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from dockline import suitetheme as theme           # noqa: E402

app = QApplication([])
theme.install(app)
from dockline import glassfix                      # noqa: E402
glassfix.install()


class Bar(QWidget):
    def __init__(self, own: bool):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.own_corners = own
        theme.frost(self, fade=False)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.Antialiasing)
        theme.paint_glass(p, QRectF(self.rect()), 14)


bars = [Bar(False), Bar(True)]
for i, bar in enumerate(bars):
    bar.setGeometry(200, 200 + i * 90, 420, 52)
    bar.show()
out = Path(__file__).with_name('out')
out.mkdir(exist_ok=True)


def shoot():
    screen = QGuiApplication.primaryScreen()
    for i, bar in enumerate(bars):
        g = bar.geometry()
        shot = screen.grabWindow(0, g.x() - 12, g.y() - 12, g.width() + 24, g.height() + 24)
        side = g.height() + 24
        left = shot.copy(0, 0, side, side).scaled(side * 6, side * 6, Qt.KeepAspectRatio, Qt.FastTransformation)
        left.save(str(out / f'corners_{"fixed" if bar.own_corners else "before"}.png'))
    print('saved')
    app.quit()


QTimer.singleShot(1500, shoot)
app.exec()
