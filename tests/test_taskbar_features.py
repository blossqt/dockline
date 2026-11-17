"""Dockline 0.2.0's taskbar features, with stand-ins: no real windows are touched, nothing is started.

    python tests\test_taskbar_features.py
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QMimeData, QUrl            # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

from dockline import shortcuts, win                    # noqa: E402
from dockline.config import Config                     # noqa: E402

APP = QApplication.instance() or QApplication([])
SYSTEM_PINS = Path(os.environ.get('ProgramData', r'C:\ProgramData')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'


def some_shortcut() -> Path:
    """A real shortcut to a program on this PC, to copy."""
    for path in sorted(SYSTEM_PINS.rglob('*.lnk')):
        if shortcuts.program_of(str(path)):
            return path
    raise unittest.SkipTest('no program shortcut found')


class Temp(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='dockline-test-'))
        self.saved_appdata = os.environ.get('APPDATA')
        os.environ['APPDATA'] = str(self.folder)

    def tearDown(self):
        if self.saved_appdata is not None:
            os.environ['APPDATA'] = self.saved_appdata
        shutil.rmtree(self.folder, ignore_errors=True)


class ShortcutTests(Temp):
    def test_reads_target_and_arguments(self):
        source = some_shortcut()
        found = shortcuts.read(str(source))
        self.assertTrue(found.target.lower().endswith('.exe'), found)
        self.assertTrue(os.path.exists(found.target), found)

    def test_plain_paths_are_their_own_target(self):
        self.assertEqual(shortcuts.read(r'C:\x\y.exe').target, r'C:\x\y.exe')
        self.assertEqual(shortcuts.program_of(r'C:\x\y.txt'), '')

    def test_explorer_pin(self):
        empty = self.folder / 'File Explorer.lnk'
        empty.write_bytes(b'\0' * 10)
        self.assertEqual(shortcuts.program_of(str(empty)), shortcuts.EXPLORER.lower())

    def test_taskbar_pins_and_keep(self):
        pins = shortcuts.pins_folder()
        pins.mkdir(parents=True)
        source = some_shortcut()
        shutil.copy(source, pins / 'B app.lnk')
        shutil.copy(source, pins / 'A app.lnk')
        (pins / 'desktop.ini').write_text('x')
        found = shortcuts.taskbar_pins()
        self.assertEqual([Path(p).name for p in found], ['A app.lnk', 'B app.lnk'])
        kept = shortcuts.keep(found[0])
        self.assertTrue(kept.startswith(str(self.folder / 'Dockline' / 'Pins')), kept)
        self.assertEqual(shortcuts.keep(kept), kept)               # already kept: as it is
        self.assertEqual(shortcuts.keep(found[0]), kept)           # the same file again: the same copy

    def test_quote(self):
        self.assertEqual(shortcuts.quote('a.txt'), 'a.txt')
        self.assertEqual(shortcuts.quote(r'C:\My files\a.txt'), r'"C:\My files\a.txt"')


class DockTests(Temp):
    def setUp(self):
        super().setUp()
        self.real_windows = win.app_windows
        win.app_windows = lambda pid: []                       # no real windows in the strip

    def tearDown(self):
        win.app_windows = self.real_windows
        super().tearDown()

    def make(self):
        from dockline.dock import Dock
        cfg = Config(None)
        cfg['widgets'] = ['start', 'apps', 'language', 'clock']
        return Dock(cfg, testing=True)

    def apps(self, dock):
        return next(t for t in dock.tiles if t.ID == 'apps')

    def test_import_pins_once_and_without_doubles(self):
        pins = shortcuts.pins_folder()
        pins.mkdir(parents=True)
        source = some_shortcut()
        shutil.copy(source, pins / 'Tool.lnk')
        dock = self.make()
        self.assertEqual(dock.import_taskbar_pins(), 1)
        self.assertTrue(dock.cfg['pins_imported'])
        self.assertEqual(dock.import_taskbar_pins(), 0)          # already there
        self.assertEqual(len(dock.cfg['pinned']), 1)

    def test_windows_join_their_shortcut_pin(self):
        source = some_shortcut()
        program = shortcuts.read(str(source)).target
        dock = self.make()
        dock.cfg['pinned'] = [str(source)]
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, program), win.Window(102, 'Two', 7, program),
                                       win.Window(103, 'Other', 8, r'C:\other\other.exe')]
        tile = self.apps(dock)
        tile.refresh()
        self.assertEqual(len(tile.groups), 2, [g.path for g in tile.groups])
        self.assertEqual(tile.groups[0].path, str(source))
        self.assertEqual([w.hwnd for w in tile.groups[0].windows], [101, 102])

    def test_drop_opens_files_in_the_app_and_pins_programs(self):
        source = some_shortcut()
        program = shortcuts.read(str(source)).target
        dock = self.make()
        dock.cfg['pinned'] = [str(source)]
        tile = self.apps(dock)
        tile.refresh()
        started = []
        real = win.shell_open
        win.shell_open = lambda prog, line='', folder=None: started.append((prog.lower(), line)) or True
        try:
            self.assertTrue(tile.open_with(tile.groups[0], [r'C:\My files\a.txt', r'C:\b.txt']))
        finally:
            win.shell_open = real
        self.assertEqual(started[0][0], program.lower())
        self.assertTrue(started[0][1].endswith(r'"C:\My files\a.txt" C:\b.txt'), started)
        self.assertTrue(tile.all_programs([r'C:\a.exe', r'C:\b.lnk']))
        self.assertFalse(tile.all_programs([r'C:\a.exe', r'C:\b.txt']))
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(r'C:\a b\c.txt')])
        self.assertEqual(tile.dragged_files(mime), [r'C:\a b\c.txt'])

    def test_app_menu_has_only_the_app(self):
        from PySide6.QtWidgets import QMenu
        source = some_shortcut()
        program = shortcuts.read(str(source)).target
        dock = self.make()
        dock.cfg['pinned'] = [str(source)]
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, program)]
        tile = self.apps(dock)
        tile.refresh()
        tile.resize(tile.sizeHint())
        centre = tile.rects()[0].center().toPoint()
        self.assertTrue(tile.owns_menu(centre))
        menu = QMenu()
        tile.menu(menu, centre)
        words = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(words, [source.stem, 'Run as administrator', 'Open file location', 'Unpin from dock',
                                 'Close window', 'End task'])
        self.assertFalse(tile.owns_menu(centre + type(centre)(tile.width() + 50, 0)))

    def test_media_card_scrolls_through_sources(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        dock = self.make()
        dock.cfg['widgets'] = ['media']
        dock.rebuild()
        tile = next(t for t in dock.tiles if t.ID == 'media')
        self.assertEqual(tile.width(), tile.height())              # a small button now
        tile.click(tile.rect().center())
        carousel = tile.carousel
        self.assertFalse(carousel.isWindow())                     # never a blank window of its own, even for a moment
        self.assertFalse(tile.empty.isWindow())
        self.assertEqual([p.session.app for p in carousel.pages], ['Music', 'Browser'])
        wheel = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, -120), Qt.NoButton,
                            Qt.NoModifier, Qt.NoScrollPhase, False)
        carousel.wheelEvent(wheel)
        self.assertEqual(carousel.index, 1)
        carousel.wheelEvent(wheel)
        self.assertEqual(carousel.index, 1)                        # the last one stays
        self.assertFalse(carousel.pages[1].buttons['next'].isEnabled())
        tile.card.close()

    def test_language_tile_draws(self):
        dock = self.make()
        tile = next(t for t in dock.tiles if t.ID == 'language')
        tile.tick()
        self.assertEqual(tile.shown, ('ENG', 'US'))
        self.assertFalse(tile.grab().isNull())


class DisplayTests(unittest.TestCase):
    """The main dock follows Windows' main display (stand-in displays and docks)."""

    def setUp(self):
        from dockline import docks

        class Screen:
            def __init__(self, name):
                self._name = name

            def name(self):
                return self._name

        class FakeDock:
            made = []

            def __init__(self, cfg, screen=None, main=True):
                self.target_screen, self.main, self.multi, self.tiles, self.others = screen, main, False, [], []
                self.alive = True
                self.stopped = False
                FakeDock.made.append(self)

            def screen_alive(self):
                return self.target_screen in docks_module.QGuiApplication.screens()

            def move_to(self, screen):
                self.target_screen = screen

            def start(self): pass
            def stop(self): self.stopped = True
            def close(self): pass
            def deleteLater(self): pass

        class Gui:
            primary = None
            shown = []

            @classmethod
            def primaryScreen(cls):
                return cls.primary

            @classmethod
            def screens(cls):
                return list(cls.shown)

        docks_module = docks
        self.docks, self.Screen, self.Gui = docks, Screen, Gui
        self.saved = docks.Dock, docks.QGuiApplication, win.taskbar_settings
        docks.Dock, docks.QGuiApplication = FakeDock, Gui
        win.taskbar_settings = lambda: (True, 0)

    def tearDown(self):
        self.docks.Dock, self.docks.QGuiApplication, win.taskbar_settings = self.saved

    def test_main_monitor_off_and_back(self):
        dell, typec = self.Screen('DISPLAY1'), self.Screen('DISPLAY2')
        self.Gui.primary, self.Gui.shown = dell, [dell, typec]
        group = self.docks.Docks(Config(None))
        group.main.target_screen = dell
        group.sync()
        self.assertIs(group.main.target_screen, dell)
        self.assertEqual(list(group.extra), ['DISPLAY2'])
        second = group.extra['DISPLAY2']
        # the main monitor is turned off: Windows makes the other one main
        self.Gui.primary, self.Gui.shown = typec, [typec]
        group.sync()
        self.assertIs(group.main.target_screen, typec)          # the full dock, with its widgets, moved
        self.assertEqual(group.extra, {})
        self.assertTrue(second.stopped)
        # and back on
        self.Gui.primary, self.Gui.shown = dell, [dell, typec]
        group.sync()
        self.assertIs(group.main.target_screen, dell)
        self.assertEqual(list(group.extra), ['DISPLAY2'])


class WindowsTests(unittest.TestCase):
    """Reads only: nothing on screen changes."""

    def test_keyboards_read(self):
        layouts = win.keyboards()
        self.assertTrue(layouts)
        self.assertTrue(win.locale_of(layouts[0]))

    def test_maximised_window_is_not_full_screen(self):
        real_zoomed = win.user32.IsZoomed
        try:
            win.user32.IsZoomed = lambda hwnd: True
            self.assertFalse(win.full_screen_in_front(os.getpid() + 1))
        finally:
            win.user32.IsZoomed = real_zoomed


if __name__ == '__main__':
    unittest.main()
