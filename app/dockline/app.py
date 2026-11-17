"""Starting and ending Dockline.

    Dockline.exe                    start the dock (a second start opens Settings in the first)
    Dockline.exe --restore-taskbar  give the Windows taskbar back after a run that ended badly
    Dockline.exe --self-test        build every widget and card off screen and exit (0 = fine)
    Dockline.exe --render <folder>  draw the dock in every style, and its cards, as PNG files
"""

from __future__ import annotations

import getpass
import os
import sys
import time
import traceback
from pathlib import Path


def _log_errors() -> None:
    def hook(kind, value, trace):
        text = ''.join(traceback.format_exception(kind, value, trace))
        sys.stderr.write(text) if sys.stderr else None
        try:
            from .config import folder
            folder().mkdir(parents=True, exist_ok=True)
            with open(folder() / 'error.log', 'a', encoding='utf-8') as log:
                log.write(f'--- {time.ctime()}\n{text}\n')
        except OSError:
            pass
    sys.excepthook = hook


def _render(folder: Path, offscreen: bool) -> int:
    """Every style's dock with every widget, and the cards that open from it."""
    from PySide6.QtWidgets import QApplication

    from . import look as L
    from .config import Config
    from .dock import Dock
    from .widgets import WIDGETS

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setFont(L.font(14))
    folder.mkdir(parents=True, exist_ok=True)
    card_tiles = ('media', 'recycle', 'volume', 'timer', 'stopwatch', 'pomodoro', 'notes', 'clock', 'language', 'ai_limits', 'claude', 'chatgpt', 'gemini')
    failures = 0
    for name in L.STYLES:
        for size in ('S', 'M', 'L') if name == 'black' else ('M',):
            cfg = Config(None)
            cfg['look'] = name
            cfg['size'] = size
            cfg['widgets'] = [w.ID for w in WIDGETS if w.ID != 'apps'][:12] if size == 'M' else cfg['widgets']
            dock = Dock(cfg, testing=True)
            dock.fit()
            dock.layout().activate()
            for tile in dock.tiles:
                tile.tick()
            dock.grab().save(str(folder / f'dock-{name}-{size}.png'))
            if size != 'M':
                continue
            cfg['widgets'] = [w.ID for w in WIDGETS if w.ID not in cfg['widgets']]
            dock.rebuild()
            dock.layout().activate()
            for tile in dock.tiles:
                tile.tick()
            dock.grab().save(str(folder / f'dock-{name}-{size}-more.png'))
            cfg['widgets'] = [w.ID for w in WIDGETS]
            dock.rebuild()
            for tile in dock.tiles:
                if tile.ID in card_tiles:
                    try:
                        tile.click(tile.rect().center())
                        card = tile.card
                        if card is None:
                            raise RuntimeError('no card opened')
                        card.adjustSize()
                        card.grab().save(str(folder / f'card-{name}-{tile.ID}.png'))
                        card.close()
                    except Exception:
                        failures += 1
                        traceback.print_exc()
            if True:                                 # Settings and the picker in every style
                from .picker import Picker
                from .settings_window import SettingsWindow
                picker = Picker(dock)
                picker.grab().save(str(folder / f'picker-{name}.png'))
                settings = SettingsWindow(dock)
                settings.grab().save(str(folder / f'settings-{name}.png'))
                settings.open_page('widgets')
                settings.grab().save(str(folder / f'settings-widgets-{name}.png'))
            dock.deleteLater()
    print(f'rendered into {folder}; {failures} card failures')
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    _log_errors()
    if '--guard' in argv:                       # the watcher: no Qt, no window
        from .guard import guard
        at = argv.index('--guard')
        return guard(int(argv[at + 1]), int(argv[at + 2]) if len(argv) > at + 2 else 0)
    if '--restore-taskbar' in argv:
        from .config import Config
        from .taskbar import Taskbar
        Taskbar(Config.load()).restore()
        return 0
    if '--self-test' in argv:                   # the build's, the release's and Hanami's check
        at = argv.index('--self-test')
        answer = argv[at + 1] if len(argv) > at + 1 and not argv[at + 1].startswith('--') else ''
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        import tempfile
        from . import __version__
        try:
            code, problem = _render(Path(tempfile.gettempdir()) / 'dockline-self-test', True), 'a card failed'
            from . import media                  # Windows' media sessions must load in the built exe too
            if code == 0 and not media.AVAILABLE:
                code, problem = 1, 'the media API did not load'
        except Exception as error:               # the answer must still be written
            code, problem = 1, repr(error)
        if answer:
            Path(answer).write_text(__version__ if code == 0 else f'FAILED {problem}', encoding='utf-8')
        return code
    if '--render' in argv:
        return _render(Path(argv[argv.index('--render') + 1]), False)

    from PySide6.QtCore import Qt
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    from . import look as L
    from . import suitetheme as theme
    from .config import Config
    from .docks import Docks

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv[:1])
    app.setApplicationName('Dockline')
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(L.app_icon())
    theme.install(app)                       # the suite's style, glass, light or dark, and frame rate
    # Not every event of the app through Python: the suite's smooth-wheel filter sits on Settings'
    # scroll area alone (settings_window). App-wide, it was being run as Qt told a closing card's
    # widgets their window was no longer active - where all five of Dockline's crashes were.
    app.removeEventFilter(theme._wheels())
    app.setFont(L.font(14))

    name = f'Dockline-{getpass.getuser()}'
    other = QLocalSocket()
    other.connectToServer(name)
    if other.waitForConnected(300):
        other.write(b'quit' if '--quit' in argv else b'settings')
        other.waitForBytesWritten(300)
        other.disconnectFromServer()
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    cfg = Config.load()
    docks = Docks(cfg)
    dock = docks.main

    def knock():
        socket = server.nextPendingConnection()
        if socket is None:
            return

        def read():
            word = bytes(socket.readAll()).strip()
            if word == b'settings':
                dock.come_back()                 # started again: the dock shows, whatever happened
                dock.open_settings()
            elif word == b'quit':
                app.quit()
        socket.readyRead.connect(read)
        if socket.bytesAvailable():          # sent before the connection was taken: no readyRead comes
            read()

    server.newConnection.connect(knock)
    docks.start()
    link = _join_hanami(app, dock)
    if not Path(cfg.path).exists():
        cfg.save()
        dock.open_settings()                 # the first start shows where everything is set
    code = app.exec()
    link.stop()
    docks.stop()
    return theme.leave(code)


def _join_hanami(app, dock):
    """Dockline in Hanami's tray menu while it runs (it has no tray icon of its own, as Keyglow):
    its Settings, and Quit - which Hanami also sends before it updates Dockline."""
    from PySide6.QtCore import QBuffer, QIODevice, QObject, Signal

    from . import look as L
    from . import suitelink

    class Clicks(QObject):
        asked = Signal(str)                  # from the link's thread to the window's

    clicks = Clicks(app)

    def chosen(item: str) -> None:
        if item in ('settings', 'open', 'show'):
            dock.come_back()
            dock.open_settings()
        elif item == 'quit':
            app.quit()

    clicks.asked.connect(chosen)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    L.render_app_icon(64).save(buffer, 'PNG')
    link = suitelink.SuiteLink('Dockline', bytes(buffer.data()), on_click=clicks.asked.emit)
    link.set_menu([{'id': 'settings', 'text': 'Dockline Settings…', 'default': True},
                   {'separator': True},
                   {'id': 'quit', 'text': 'Quit Dockline'}], tooltip='Dockline')
    link.start()
    link.clicks = clicks                     # kept alive with the link
    return link
