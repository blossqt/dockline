"""One dock per display, as Windows' "Show my taskbar on all displays" says: the main display's dock
has the widgets and Settings; the others have Start, their apps and the clock."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication

from . import win
from .dock import Dock


class Docks(QObject):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.main = Dock(cfg)
        self.extra: dict[str, Dock] = {}
        self.timer = QTimer(self)                 # Windows' setting changed, or a display came or went
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.sync)

    def all(self) -> list[Dock]:
        return [self.main, *self.extra.values()]

    def start(self) -> None:
        self.main.start()
        self.sync()
        self.timer.start()
        app = QGuiApplication.instance()
        app.screenAdded.connect(lambda _: QTimer.singleShot(600, self.sync))
        app.screenRemoved.connect(lambda _: QTimer.singleShot(0, self.sync))

    def sync(self) -> None:
        enabled, _ = win.taskbar_settings()
        primary = self.main.target_screen
        screens = {s.name(): s for s in QGuiApplication.screens()}
        want = {name: s for name, s in screens.items() if s is not primary} if enabled else {}
        for name in list(self.extra):
            if name not in want or self.extra[name].target_screen is not want[name]:
                dock = self.extra.pop(name)
                dock.stop()
                dock.close()
                dock.deleteLater()
        for name, screen in want.items():
            if name not in self.extra:
                dock = Dock(self.cfg, screen=screen, main=False)
                self.extra[name] = dock
                dock.multi = True
                dock.start()
        multi = bool(self.extra)
        self.main.others = list(self.extra.values())
        for dock in self.all():
            if dock.multi != multi:
                dock.multi = multi
                for tile in dock.tiles:
                    if tile.ID == 'apps':
                        tile.refresh()

    def stop(self) -> None:
        self.timer.stop()
        for dock in self.all():
            dock.stop()
