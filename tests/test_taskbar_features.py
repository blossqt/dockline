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
from PySide6.QtWidgets import QApplication, QLabel    # noqa: E402

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

    def test_closed_card_is_deleted_by_qt_alone(self):
        import shiboken6
        from dockline.card import Card
        dock = self.make()
        tile = next(t for t in dock.tiles if t.ID == 'clock')
        tile.click(tile.rect().center())
        card = tile.card
        tile.card = None                                       # the tile lets go, as card_closed does
        card.close()
        self.assertIn(card, Card.closing)                     # still held: Python can't free it
        self.assertTrue(shiboken6.isValid(card))
        for _ in range(5):
            APP.processEvents()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(shiboken6.isValid(card))             # Qt deleted it, from its own loop
        Card.release_closed()
        self.assertNotIn(card, Card.closing)

    def test_magnified_apps_follow_the_dock(self):
        """The magnifier's layer was placed once, as it showed: with the dock still sliding up, or
        widening later, the grown icons ran past its top edge and were cut off."""
        from dockline import motion
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, r'C:\a\one.exe'),
                                       win.Window(102, 'Two', 8, r'C:\b\two.exe')]
        dock = self.make()
        dock.show()
        tile = self.apps(dock)
        tile.refresh()
        dock.layout().activate()
        layer = motion.Magnifier(tile)
        tile._magnifier = layer
        layer.follow(0.5)
        self.assertTrue(layer.isVisible())

        def expected():
            side, up = layer.margin()
            corner = tile.mapToGlobal(tile.rect().topLeft())
            return (corner.x() - side, corner.y() - up)

        self.assertEqual((layer.x(), layer.y()), expected())
        dock.move(dock.x() + 37, dock.y() - 23)                # the dock slides up and along
        for _ in range(3):
            APP.processEvents()
        self.assertEqual((layer.x(), layer.y()), expected())
        layer.step()                                            # and each frame keeps it there
        self.assertEqual((layer.x(), layer.y()), expected())
        layer.drop()
        dock.hide()

    def test_open_tray_keeps_the_dock(self):
        """Auto-hide: the pointer away from the dock - in Windows' hidden-icons panel - and the dock
        stays while that panel is open; it goes as before once the panel has closed."""
        import time as clock
        from unittest import mock
        from PySide6.QtCore import QPoint
        from dockline import dock as dock_module, veil
        dock = self.make()
        dock.show()
        far = QPoint(dock.x() + dock.width() // 2, dock.y() - 400)          # well above it: in the tray panel
        tray = [4242]
        with mock.patch.object(dock_module.QCursor, 'pos', staticmethod(lambda: far)),                 mock.patch.object(veil, 'panel_window', lambda: tray[0]),                 mock.patch.object(dock, 'autohide', lambda: True),                 mock.patch.object(dock, 'keep_in_place', lambda: None),                 mock.patch.object(dock, 'slide', lambda coming, then=None: setattr(dock, 'slid', coming)):
            dock.testing = False                                   # tray_open looks only in a real dock
            try:
                for _ in range(3):
                    dock.watch_pointer()
                    dock.away_since = clock.monotonic() - 1 if dock.away_since else None
                self.assertFalse(dock.tucked)
                tray[0] = 0                                        # a click elsewhere closed the panel
                dock.watch_pointer()
                dock.away_since = clock.monotonic() - 1
                dock.watch_pointer()
                self.assertTrue(dock.tucked)
            finally:
                dock.testing = True
        dock.hide()

    def test_settings_export_and_import(self):
        from dockline import config
        source = some_shortcut()
        kept = shortcuts.keep(str(source))                       # a pin Dockline keeps in its Pins folder
        cfg = Config(None)
        cfg['pinned'] = [kept, r'C:\Tools\tool.exe']
        cfg['widgets'] = ['start', 'apps', 'clock']
        cfg['look'] = 'midnight'
        cfg['taskbar_saved'] = 3
        out = self.folder / 'export.json'
        config.export_settings(cfg, out)
        Path(kept).unlink()                                       # a new PC: the Pins folder is empty
        fresh = Config(None)
        config.import_settings(fresh, out)
        self.assertEqual(fresh['widgets'], ['start', 'apps', 'clock'])
        self.assertEqual(fresh['look'], 'midnight')
        self.assertIsNone(fresh['taskbar_saved'])                # this PC's own state is never taken
        self.assertTrue(Path(fresh['pinned'][0]).exists())        # the shortcut was put back
        self.assertEqual(fresh['pinned'][1], r'C:\Tools\tool.exe')
        self.assertTrue(fresh['pins_imported'])
        wrong = self.folder / 'other.json'
        wrong.write_text('{"look": "black"}', 'utf-8')
        with self.assertRaises(config.ImportError_):
            config.import_settings(fresh, wrong)

    def test_previews_sit_low_and_peek(self):
        from unittest import mock
        from dockline import previews
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, r'C:\a\one.exe'),
                                       win.Window(102, 'Two', 7, r'C:\a\one.exe')]
        dock = self.make()
        dock.show()
        tile = self.apps(dock)
        tile.refresh()
        dock.layout().activate()
        panel = previews.Previews(tile)
        peeks = []
        with mock.patch.object(previews, 'peek', lambda on, hwnd=0, keep=0: peeks.append((on, hwnd))):
            panel.show_for(0)
            self.assertEqual(panel.y() + panel.height(), dock.mapToGlobal(dock.rect().topLeft()).y() - previews.ABOVE)
            panel.hover = 1
            panel.start_peek()                                    # resting on the second window's preview
            self.assertEqual(peeks[-1], (True, 102))
            panel.close_now()
            self.assertEqual(peeks[-1], (False, 0))
        dock.hide()

    def test_previews_close_start_that_covers_them(self):
        """Start brought the dock up over a full-screen game and covers the previews: the panel takes
        the front (Start closes as on a click elsewhere), or, if Windows refuses, Esc goes to Start."""
        from unittest import mock
        from dockline import previews
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, r'C:\a\one.exe'),
                                       win.Window(102, 'Two', 7, r'C:\a\one.exe')]
        dock = self.make()
        dock.show()
        tile = self.apps(dock)
        tile.refresh()
        dock.layout().activate()
        panel = previews.Previews(tile)
        START = 777
        state = {'front': START, 'granted': True, 'covers': True}
        activated, escaped = [], []

        def activate(hwnd):
            activated.append(hwnd)
            if state['granted']:
                state['front'] = hwnd

        with mock.patch.object(win, 'shell_over', lambda hwnd: START if state['covers'] and state['front'] == START else 0), \
                mock.patch.object(win, 'activate', activate), \
                mock.patch.object(win, 'foreground', lambda: state['front']), \
                mock.patch.object(win, 'dismiss_shell', lambda front: escaped.append(front) or True):
            dock.testing = False                                  # only a real dock clears Start away
            try:
                panel.show_for(0)
                self.assertEqual(activated, [int(panel.winId())])
                self.assertEqual(escaped, [])                     # the panel came to the front: no key
                panel.close_now()
                state.update(front=START, granted=False)
                activated.clear()
                panel.show_for(0)
                self.assertEqual(escaped, [START])                # refused: Esc, to Start alone
                panel.close_now()
                state.update(front=START, covers=False)           # Start on another screen
                activated.clear()
                escaped.clear()
                panel.show_for(0)
                self.assertEqual((activated, escaped), ([], []))
                panel.close_now()
            finally:
                dock.testing = True
        dock.hide()

    def test_dock_stays_while_previews_are_open_over_a_full_screen_app(self):
        """Start closed for the previews and the game is in front again: the dock goes once they close."""
        from unittest import mock
        dock = self.make()
        dock.show()
        open_ = [True]
        with mock.patch.object(win, 'full_screen_in_front', lambda pid, device=None: True), \
                mock.patch.object(dock, 'previews_open', lambda: open_[0]), \
                mock.patch.object(dock, 'slide', lambda coming, then=None: setattr(dock, 'slid', coming)):
            dock.check_full_screen()
            self.assertFalse(dock.full_screen)
            self.assertTrue(dock.full_later.isActive())
            open_[0] = False
            dock.check_full_screen()
            self.assertTrue(dock.full_screen)
            self.assertFalse(dock.slid)
        dock.full_watch.stop()
        dock.full_later.stop()
        dock.hide()

    def test_media_progress_and_jump(self):
        dock = self.make()
        dock.cfg['widgets'] = ['media']
        dock.rebuild()
        tile = next(t for t in dock.tiles if t.ID == 'media')
        tile.click(tile.rect().center())
        page = tile.carousel.pages[0]
        self.assertTrue(page.progress.isVisibleTo(page))
        self.assertTrue(page.progress.seekable())
        page.seek(100.0)                                          # testing: shown, nothing sent
        self.assertAlmostEqual(tile.carousel.pages[0].session.position, 100.0)
        self.assertFalse(tile.carousel.pages[1].progress.isVisibleTo(tile.carousel.pages[1]))   # no length given
        tile.card.close()

    def test_sound_card_lists_apps(self):
        dock = self.make()
        dock.cfg['widgets'] = ['volume']
        dock.rebuild()
        tile = next(t for t in dock.tiles if t.ID == 'volume')
        rows = tile.app_rows()
        names = [label.text() for label in rows.findChildren(QLabel)]
        self.assertIn('System sounds', names)
        self.assertIn('Music', names)

    def test_language_tile_draws(self):
        dock = self.make()
        tile = next(t for t in dock.tiles if t.ID == 'language')
        tile.tick()
        self.assertEqual(tile.shown, ('ENG', 'US'))
        self.assertFalse(tile.grab().isNull())

    # -- 0.5.10: the dock's motion
    def moving(self):
        """Animations on, whatever this PC's setting, for the rest of the test."""
        from unittest import mock
        from dockline import motion
        patch = mock.patch.object(motion, 'on', lambda: True)
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def finish(strip, key, share: float) -> None:
        """Move key's change on to share of its time, then draw a frame."""
        start, low, high, ms = strip.changes[key]
        strip.changes[key] = (start - ms / 1000 * share, low, high, ms)
        strip.frame()

    def test_apps_grow_in_and_shrink_away_keeping_their_size(self):
        """A new app used to make every icon shrink for a moment (the strip had its room before the
        dock had widened), and one closing made those after it jump a whole place."""
        self.moving()
        shown = [win.Window(101, 'One', 7, r'C:\a\one.exe'), win.Window(102, 'Two', 8, r'C:\b\two.exe')]
        win.app_windows = lambda pid: list(shown)
        dock = self.make()
        dock.show()
        strip = self.apps(dock)
        strip.refresh()
        dock.fit_now()
        tile = float(dock.tile_height)
        shown.append(win.Window(103, 'Three', 9, r'C:\c\three.exe'))
        strip.refresh()
        new = r'c:\c\three.exe'
        self.assertEqual(strip.presence[new], 0.0)                 # coming: no room yet
        self.finish(strip, new, 0.4)
        self.assertTrue(0.0 < strip.presence[new] < 1.0)
        self.assertEqual(strip.slot, tile)                         # the others keep their size
        self.assertAlmostEqual(strip.width(), strip.wanted(strip.weight()), delta=2)
        self.finish(strip, new, 1.0)
        self.assertEqual(strip.presence, {})
        self.assertEqual(len(strip.rects()), 3)
        shown.pop(0)                                               # the first app closes
        strip.refresh()
        gone = r'c:\a\one.exe'
        self.assertIn(gone, strip.leaving)
        self.assertEqual([e.key for e in strip.display()][0], gone)     # shrinking where it was
        self.assertEqual(strip.display()[0].index, -1)
        self.assertEqual(len(strip.rects()), 2)                    # not an app to point at any more
        self.finish(strip, gone, 0.5)
        self.assertEqual(strip.slot, tile)
        self.finish(strip, gone, 1.0)
        self.assertEqual(strip.leaving, {})
        self.assertEqual([e.key for e in strip.display()], [r'c:\b\two.exe', new])
        strip.retire()
        dock.hide()

    def test_apps_jump_straight_to_their_places_without_animations(self):
        from unittest import mock
        from dockline import motion
        shown = [win.Window(101, 'One', 7, r'C:\a\one.exe')]
        win.app_windows = lambda pid: list(shown)
        dock = self.make()
        dock.show()
        strip = self.apps(dock)
        strip.refresh()
        with mock.patch.object(motion, 'on', lambda: False):
            shown.append(win.Window(102, 'Two', 8, r'C:\b\two.exe'))
            strip.refresh()
        self.assertEqual((strip.presence, strip.changes, strip.leaving), ({}, {}, {}))
        self.assertEqual(len(strip.display()), 2)
        dock.hide()

    def test_started_app_hops_and_breathes_until_its_window_shows(self):
        from dockline import motion, widgets
        self.moving()
        source = some_shortcut()
        program = shortcuts.read(str(source)).target
        shown = []
        win.app_windows = lambda pid: list(shown)
        dock = self.make()
        dock.cfg['pinned'] = [str(source)]
        dock.show()
        strip = self.apps(dock)
        strip.refresh()
        started = []
        real_open = widgets._open
        widgets._open = started.append
        try:
            strip.click(strip.rects()[0].center().toPoint())
        finally:
            widgets._open = real_open
        key = strip.keys[0]
        self.assertEqual(started, [str(source)])
        self.assertIn(key, strip.launches)
        self.assertIn(key, strip.hops)
        self.assertTrue(strip.animator.isActive())
        self.assertGreater(strip.grab().width(), 0)                # drawn mid-hop
        shown.append(win.Window(201, 'Its window', 5, program))
        strip.refresh()
        self.assertNotIn(key, strip.launches)                      # its window showed
        # started through a launcher: another program's new window comes to the front
        strip.launch(key)
        shown.append(win.Window(202, 'Launched', 6, r'C:\other\real.exe'))
        real_front = win.foreground
        win.foreground = lambda: 202
        try:
            strip.refresh()
        finally:
            win.foreground = real_front
        self.assertNotIn(key, strip.launches)
        # nothing shows: it stops after LAUNCH_MOST seconds
        strip.launch(key)
        strip.launches[key]['at'] -= motion.LAUNCH_MOST + 1
        strip.end_launches()
        self.assertEqual(strip.launches, {})
        strip.retire()
        dock.hide()

    def test_attention_pulses_then_holds(self):
        from dockline import motion
        self.moving()
        self.assertEqual(motion.pulse(0.0), 0.0)
        self.assertAlmostEqual(motion.pulse(motion.PULSE / 2), 1.0)
        self.assertEqual(motion.pulse(motion.PULSE * motion.PULSES + 5), 1.0)       # then held
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, r'C:\a\one.exe')]
        dock = self.make()
        dock.show()
        strip = self.apps(dock)
        strip.refresh()
        real_front = win.foreground
        win.foreground = lambda: 999
        try:
            strip.flash(101)
        finally:
            win.foreground = real_front
        self.assertIn(101, strip.flash_at)
        self.assertEqual(strip.animator.interval(), motion.PULSE_EVERY)              # pulses alone: slow frames
        strip.flash(101, False)
        self.assertNotIn(101, strip.flash_at)
        strip.retire()
        dock.hide()

    def test_glass_tint_keeps_text_readable_where_the_backdrop_is_bright(self):
        """A dark card half over a white page: the tint over the page is strong enough for white text,
        and over the dark half stays as clear as the glass is set. A light card does the opposite."""
        from PySide6.QtGui import QColor, QImage, QPixmap
        from dockline import look as L
        image = QImage(240, 240, QImage.Format_RGB32)
        image.fill(QColor(20, 20, 24))
        for y in range(120, 240):
            for x in range(240):
                image.setPixelColor(x, y, QColor(250, 250, 250))
        backdrop = QPixmap.fromImage(image)
        fill = QColor(0, 0, 0, 89)
        tint = L.readable_tint(backdrop, fill, dark=True).toImage()
        top, bottom = tint.pixelColor(10, 1).alpha(), tint.pixelColor(10, tint.height() - 1).alpha()
        self.assertEqual(top, 89)                                        # dark behind: as set
        under = 250 * (1 - bottom / 255)
        self.assertLessEqual(under, L.READ_DARK + 2)                     # white behind: dark enough to read
        light = L.readable_tint(backdrop, QColor(255, 255, 255, 80), dark=False).toImage()
        self.assertEqual(light.pixelColor(10, light.height() - 1).alpha(), 80)
        over = 20 + (255 - 20) * light.pixelColor(10, 1).alpha() / 255
        self.assertGreaterEqual(over, L.READ_LIGHT - 2)

    def test_cards_and_menus_close_start_covering_them_first(self):
        """Start in front over where a card or menu opens: it is closed first (Esc, only while it is in
        front), as the previews close it; elsewhere, nothing is pressed."""
        from unittest import mock
        from PySide6.QtCore import QRect
        from dockline import dock as dock_module
        dock = self.make()
        dock.testing = False
        front = [77]
        pressed = []
        with mock.patch.object(win, 'foreground', lambda: front[0]), \
                mock.patch.object(win, 'window_pid', lambda hwnd: 1), \
                mock.patch.object(win, 'process_path', lambda pid: r'C:\Windows\StartMenuExperienceHost.exe'), \
                mock.patch.object(win, 'window_rect', lambda hwnd: (0, 300, 700, 1040)), \
                mock.patch.object(win, 'press', lambda *keys: (pressed.append(keys), front.__setitem__(0, 5))), \
                mock.patch.object(dock_module, 'SHELL_FADE_MS', 1):
            try:
                dock.clear_shell_before(QRect(1200, 600, 300, 400))      # Start is elsewhere
                self.assertEqual(pressed, [])
                dock.clear_shell_before(QRect(400, 600, 300, 400))       # Start covers it
                self.assertEqual(pressed, [(win.VK_ESCAPE,)])
            finally:
                dock.testing = True
        dock.hide()

    def test_bar_glass_leaves_dockline_windows_out_of_its_picture(self):
        """The bar's glass paints Dockline's own windows (magnified icons, previews, cards) out of the
        screen picture it blurs, as it does dragged images: a window of this thread is found where it
        lies in a part of the screen, relative to it. (A real window, off every screen.)"""
        import ctypes
        from ctypes import wintypes
        from PySide6.QtCore import QRect
        from dockline import glassfix, suitetheme as theme
        user32 = ctypes.WinDLL('user32')
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        hwnd = user32.CreateWindowExW(0x08000080, 'STATIC', 'test', 0x90000000,     # tool, no-activate; popup, visible
                                      30000, 30000, 50, 30, None, None, None, None)
        self.assertTrue(hwnd)
        original = theme._drag_images
        try:
            glassfix.install()
            glassfix.install()                                           # once only, however often called
            self.assertIs(theme._drag_images.__closure__[0].cell_contents, original)
            found = theme._drag_images(QRect(29990, 29995, 100, 100))
            self.assertIn(QRect(10, 5, 50, 30), found)
            self.assertEqual(glassfix.own_windows(QRect(0, 0, 100, 100)), [])     # elsewhere: nothing
        finally:
            theme._drag_images = original
            user32.DestroyWindow(hwnd)

    def test_bar_glass_keeps_its_picture_while_a_card_is_open(self):
        """A card open just above the bar holds the bar's glass: no new picture of the screen (it would
        only catch the card, and thin the bar's tint); with the card gone, pictures are taken again."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QWidget
        from dockline import glassfix, suitetheme as theme
        dock = self.make()
        dock.setGeometry(400, 900, 600, 60)
        dock.show()
        card = QWidget(None, Qt.FramelessWindowHint | Qt.Tool)
        card.setGeometry(500, 600, 300, 280)                  # 20 px above the bar
        far = QWidget(None, Qt.FramelessWindowHint | Qt.Tool)
        far.setGeometry(1800, 100, 200, 200)
        taken = []
        original_capture, original_drag = theme._capture_later, theme._drag_images
        theme._capture_later = lambda window, final: taken.append(window) or True
        try:
            glassfix.install()
            far.show()
            self.assertTrue(theme._capture_later(dock, True))
            self.assertEqual(taken, [dock])                   # a window elsewhere: taken as before
            card.show()
            self.assertTrue(dock.glass_held())
            theme._capture_later(dock, True)
            self.assertEqual(taken, [dock])                   # held while the card is open
            card.hide()
            theme._capture_later(dock, True)
            self.assertEqual(taken, [dock, dock])
        finally:
            theme._capture_later, theme._drag_images = original_capture, original_drag
            card.close()
            far.close()
            dock.hide()

    def test_bar_glass_corners_and_sliding(self):
        """The bar keeps its own corners after the shared glass asks Windows for its own, and while it
        slides its glass waits to be pictured once it has settled."""
        from unittest import mock
        from dockline import glassfix, suitetheme as theme
        dock = self.make()
        dock.own_corners = True
        set_dwm, accents, taken = [], [], []
        originals = theme._apply_glass, theme._capture_later, theme._drag_images
        theme._capture_later = lambda window, final: taken.append(final) or True
        try:
            with mock.patch.object(theme, '_dwm', lambda hwnd, attribute, value: set_dwm.append((attribute, value))), \
                    mock.patch.object(theme, '_accent', lambda hwnd, state, color=0: accents.append(state)), \
                    mock.patch.object(theme, '_frameless', lambda window: True):
                glassfix.install()
                theme._apply_glass(dock)
            self.assertEqual(set_dwm[-1], (theme._DWMWA_WINDOW_CORNER_PREFERENCE, 1))   # not rounded by Windows
            self.assertEqual(accents[-1], theme._ACCENT_DISABLED)                      # nor blurred behind
            dock.sliding = True
            self.assertFalse(theme._capture_later(dock, False))      # along the slide: none
            self.assertTrue(theme._capture_later(dock, True))        # settled: one
            dock.sliding = False
            theme._capture_later(dock, False)
            self.assertEqual(taken, [True, False])
        finally:
            theme._apply_glass, theme._capture_later, theme._drag_images = originals
            dock.hide()

    def test_magnified_icons_rest_when_the_pointer_is_elsewhere(self):
        """Magnified with no word from Qt that the pointer left (a menu held it): the dock's own look at
        the pointer rests them; a menu or card opening rests them too."""
        from unittest import mock
        from PySide6.QtCore import QPoint
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, r'C:\a\one.exe')]
        dock = self.make()
        dock.show()
        strip = self.apps(dock)
        strip.refresh()
        followed = []
        layer = mock.Mock(want=1.0)
        strip._magnifier = layer
        with mock.patch.object(strip, 'follow_pointer', lambda pos: followed.append(pos)):
            inside = strip.mapToGlobal(QPoint(strip.width() // 2, strip.height() // 2))
            strip.check_pointer(inside)
            self.assertEqual(followed, [])                   # over the apps: still magnified
            strip.check_pointer(inside + QPoint(0, -400))
            self.assertEqual(followed, [None])               # elsewhere: back to rest
            layer.want = 0.0
            strip.check_pointer(inside + QPoint(0, -400))
            self.assertEqual(followed, [None])               # resting already: nothing to do
            dock.rest_icons()
            self.assertEqual(followed, [None, None])
        strip._magnifier = None
        strip.retire()
        dock.hide()

    def test_menus_open_above_the_bar(self):
        from unittest import mock
        from PySide6.QtCore import QPoint
        from dockline import dock as dock_module
        dock = self.make()
        dock.setGeometry(400, 1000, 700, 52)
        shown = []
        menu = dock.new_menu()
        menu.addAction('Task Manager')
        with mock.patch.object(dock_module.GlassMenu, 'exec', lambda m, where: shown.append(where)):
            dock.show_menu(menu, QPoint(600, 1030))
            dock.show_menu(menu, QPoint(600, 1030), above=False)
        size = menu.sizeHint()
        self.assertEqual(shown[0].y(), 1000 - dock_module.ABOVE_BAR - size.height())   # clear of the bar
        self.assertEqual(shown[1], QPoint(600, 1030))                                  # a preview's: at the pointer
        dock.hide()

    def test_menu_opens_task_manager(self):
        """The dock's right-click menu has Task Manager, as the Windows taskbar's has."""
        from unittest import mock
        from PySide6.QtCore import QPoint
        from dockline import dock as dock_module
        dock = self.make()
        shown, opened = [], []

        def exec_(menu, *_):
            shown.append([a.text() for a in menu.actions() if a.text()])
            next(a for a in menu.actions() if a.text() == 'Task Manager').trigger()

        with mock.patch.object(dock_module.GlassMenu, 'exec', exec_), \
                mock.patch.object(win, 'task_manager', lambda: opened.append(1)):
            dock.context_menu(None, QPoint(0, 0), QPoint(0, 0))
        self.assertEqual(shown[0][:2], ['Task Manager', 'Settings…'])
        self.assertEqual(opened, [1])
        dock.hide()

    def test_alerts_bring_a_hidden_dock_out_briefly(self):
        """An app requesting attention, a timer ending or a notification: a tucked dock comes out for a
        moment, once per request; never with alerts off, without auto-hide or over a full-screen app."""
        import time as clock
        from unittest import mock
        from dockline import dock as dock_module
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, r'C:\a\one.exe')]
        dock = self.make()
        dock.show()
        strip = self.apps(dock)
        strip.refresh()
        slides = []

        def tuck():
            dock.tucked, dock.away_since = True, None

        with mock.patch.object(dock, 'autohide', lambda: True), \
                mock.patch.object(dock, 'place', lambda: None), \
                mock.patch.object(dock, 'slide', lambda coming, then=None: slides.append(coming)), \
                mock.patch.object(win, 'foreground', lambda: 999):
            tuck()
            dock.shell_notice(win.HSHELL_FLASH, 101)
            self.assertFalse(dock.tucked)
            self.assertEqual(slides, [True])
            left = dock.away_since - clock.monotonic()
            self.assertAlmostEqual(left, dock_module.ALERT_SLIDE + dock_module.ALERT_HOLD, delta=0.1)
            tuck()
            dock.shell_notice(win.HSHELL_FLASH, 101)               # the same request flashing on: no more
            self.assertTrue(dock.tucked)
            dock.away_since = clock.monotonic() + 0.1              # out and about to go: held for a new one
            dock.tucked = False
            dock.notice_arrived()
            self.assertGreater(dock.away_since - clock.monotonic(), 1.0)
            self.assertEqual(len(slides), 1)
            tuck()
            from dockline.widgets import REGISTRY
            timer = REGISTRY['timer'](dock)
            timer.ends = clock.monotonic() - 1
            with mock.patch('ctypes.windll.user32.MessageBeep', lambda kind: None):
                timer.tick()
            self.assertFalse(dock.tucked)                          # the timer's end brought it out
            tuck()
            dock.full_screen = True
            dock.notice_arrived()
            self.assertTrue(dock.tucked)
            dock.full_screen = False
            dock.cfg['alerts'] = False
            dock.notice_arrived()
            self.assertTrue(dock.tucked)
            dock.cfg['alerts'] = True
        dock.notice_arrived()                                      # auto-hide off (a test dock): nothing
        self.assertTrue(dock.tucked)
        strip.retire()
        dock.hide()

    def test_pinned_icon_dragged_off_the_dock_is_unpinned(self):
        from PySide6.QtCore import QPoint
        self.moving()
        source = some_shortcut()
        shown = [win.Window(101, 'One', 7, r'C:\a\one.exe')]
        win.app_windows = lambda pid: list(shown)
        dock = self.make()
        dock.cfg['pinned'] = [str(source)]
        dock.show()
        strip = self.apps(dock)
        strip.refresh()
        strip.settle_apps()                                        # the pin, in its place at once
        index = next(i for i, group in enumerate(strip.groups) if group.pinned)
        pinned_key = strip.keys[index]
        here = strip.rects()[index].center().toPoint()
        strip.press_index = index
        strip.begin_icon_drag(here)
        strip.drag_icon(QPoint(here.x(), -dock.tile_height * 3))  # well past the dock
        self.assertTrue(strip.torn)
        self.assertEqual(strip.changes[pinned_key][2], 0.0)       # its room closes
        strip.drag_icon(QPoint(here.x(), here.y()))                # back over the dock: it opens again
        self.assertFalse(strip.torn)
        self.assertEqual(strip.changes[pinned_key][2], 1.0)
        strip.drag_icon(QPoint(here.x(), -dock.tile_height * 3))
        strip.end_icon_drag()
        self.assertEqual(dock.cfg['pinned'], [])
        self.assertNotIn(pinned_key, strip.keys)
        # an open app that is not pinned stays in the row, however far it is pulled
        strip.settle_apps()
        index = strip.keys.index(r'c:\a\one.exe')
        strip.press_index = index
        here = strip.rects()[index].center().toPoint()
        strip.begin_icon_drag(here)
        strip.drag_icon(QPoint(here.x(), -dock.tile_height * 3))
        self.assertFalse(strip.torn)
        strip.end_icon_drag()
        self.assertIn(r'c:\a\one.exe', strip.keys)
        strip.retire()
        dock.hide()

    def test_gauges_glide_and_figures_roll(self):
        import time
        from dockline import motion
        self.moving()
        dock = self.make()
        dock.show()
        tile = self.apps(dock)
        self.assertEqual(tile.eased('x', 0.2), 0.2)                  # the first reading as it is
        self.assertEqual(tile.eased('x', 0.8), 0.2)                  # then a glide from there
        deadline = time.perf_counter() + 2
        while time.perf_counter() < deadline and abs(tile.eased('x', 0.8) - 0.8) > 1e-6:
            APP.processEvents()
        self.assertAlmostEqual(tile.eased('x', 0.8), 0.8, places=5)
        roll = tile.roll('figures')
        roll.set('37%')
        roll.set('42%')
        self.assertTrue(roll.up)
        self.assertEqual(roll.t, 0.0)
        countdown = tile.roll('timer')
        countdown.set('04:59')
        countdown.set('04:58')
        self.assertFalse(countdown.up)                               # a countdown rolls down
        self.assertEqual(motion._number('1:02:03'), 10203)
        dock.hide()

    def test_previews_glide_from_app_to_app(self):
        import time
        from unittest import mock
        from dockline import motion, previews
        self.moving()
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, r'C:\a\one.exe'),
                                       win.Window(102, 'Two', 8, r'C:\b\two.exe'),
                                       win.Window(103, 'Three', 8, r'C:\b\two.exe')]
        dock = self.make()
        dock.show()
        tile = self.apps(dock)
        tile.refresh()
        dock.layout().activate()
        panel = previews.Previews(tile)
        dock.testing = False                                       # animate, as on screen
        try:
            with mock.patch.object(previews.Previews, 'clear_shell', lambda self: None), \
                    mock.patch.object(previews.Previews, 'take_band', lambda self, band: None):
                panel.show_for(0)
                first = panel.geometry()
                panel.show_for(1)                                    # along to the next app
                self.assertIsNotNone(panel._glide)
                self.assertEqual(panel.geometry().x(), first.x())  # from where it was
                self.assertLess(panel.reveal, 1.0)
                deadline = time.perf_counter() + 2
                while panel._glide is not None and time.perf_counter() < deadline:
                    APP.processEvents()
                self.assertEqual(panel.reveal, 1.0)
                self.assertGreater(panel.width(), first.width())   # two windows' previews
                panel.close_now()
                self.assertIsNone(panel.band)
        finally:
            dock.testing = True
        dock.hide()

    def test_previews_act_as_the_taskbars(self):
        """The close button turns red under the pointer and closes only on a release over it; a click
        counts only where it was pressed; the middle button closes; the right one opens the window's
        own menu, its items greyed as its state says."""
        from unittest import mock
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from dockline import previews
        win.app_windows = lambda pid: [win.Window(101, 'One', 7, r'C:\a\one.exe'),
                                       win.Window(102, 'Two', 7, r'C:\a\one.exe')]
        dock = self.make()
        dock.show()
        tile = self.apps(dock)
        tile.refresh()
        panel = previews.Previews(tile)
        panel.show_for(0)
        closed, shown, menus = [], [], []

        def mouse(kind, pos, button=Qt.LeftButton):
            buttons = button if kind != QEvent.MouseButtonRelease else Qt.NoButton
            event = QMouseEvent(kind, QPointF(pos), QPointF(panel.mapToGlobal(pos.toPoint())), button, buttons,
                                Qt.NoModifier)
            {QEvent.MouseMove: panel.mouseMoveEvent, QEvent.MouseButtonPress: panel.mousePressEvent,
             QEvent.MouseButtonRelease: panel.mouseReleaseEvent}[kind](event)

        close = panel.close_box(1).center()
        thumb = QPointF(panel.box(0).center())
        with mock.patch.object(win, 'close', closed.append), \
                mock.patch.object(win, 'activate', shown.append), \
                mock.patch.object(win, 'window_commands', lambda hwnd: [('Restore', win.SC_RESTORE, False),
                                                                       ('Close', win.SC_CLOSE, True)]), \
                mock.patch.object(dock, 'show_menu', lambda menu, where, above=True: menus.append(
                    [(a.text(), a.isEnabled()) for a in menu.actions() if a.text()])), \
                mock.patch.object(previews, 'peek', lambda *a, **k: None):
            mouse(QEvent.MouseMove, close)
            self.assertTrue(panel.on_close)                                   # red under the pointer
            panel.grab()                                                      # and drawn so
            mouse(QEvent.MouseButtonPress, close)
            mouse(QEvent.MouseMove, thumb)
            mouse(QEvent.MouseButtonRelease, thumb)                           # let go elsewhere: nothing
            self.assertEqual((closed, shown), ([], []))
            self.assertFalse(panel.on_close)
            mouse(QEvent.MouseMove, close)
            mouse(QEvent.MouseButtonPress, close)
            mouse(QEvent.MouseButtonRelease, close)
            self.assertEqual(closed, [102])
            mouse(QEvent.MouseButtonPress, thumb, Qt.MiddleButton)
            mouse(QEvent.MouseButtonRelease, thumb, Qt.MiddleButton)
            self.assertEqual(closed, [102, 101])
            mouse(QEvent.MouseButtonPress, thumb, Qt.RightButton)
            mouse(QEvent.MouseButtonRelease, thumb, Qt.RightButton)
            self.assertEqual(menus, [[('Restore', False), ('Close', True)]])
            mouse(QEvent.MouseButtonPress, thumb)
            mouse(QEvent.MouseButtonRelease, thumb)
            self.assertEqual(shown, [101])
        panel.close_now()
        tile.retire()
        dock.hide()

    def test_window_menu_follows_the_windows_state(self):
        from unittest import mock
        style = win.WS_THICKFRAME | win.WS_MINIMIZEBOX | win.WS_MAXIMIZEBOX
        with mock.patch.object(win.user32, 'GetWindowLongPtrW', lambda hwnd, which: style), \
                mock.patch.object(win.user32, 'IsIconic', lambda hwnd: 0), \
                mock.patch.object(win.user32, 'IsZoomed', lambda hwnd: 1):
            items = {name: allowed for name, _, allowed in win.window_commands(5)}
        self.assertEqual(items, {'Restore': True, 'Move': False, 'Size': False, 'Minimize': True,
                                 'Maximize': False, 'Close': True})

    def test_frosted_band_leaves_out_the_magnified_icons(self):
        from PySide6.QtCore import QRect
        from dockline import look as L
        band = QRect(0, 100, 400, 120)
        whole = L.frosted_band(band)
        if whole is None:
            self.skipTest('no screen to take here')
        part = L.frosted_band(band, QRect(150, 180, 100, 90))        # the icons cover its foot
        self.assertIsNotNone(part)
        self.assertEqual(part.size(), whole.size())                 # carried over the rows left out
        self.assertIsNone(L.frosted_band(band, QRect(0, 90, 400, 200)))   # nothing left to take


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


class StyleTests(unittest.TestCase):
    def test_settings_colours_stay_valid_at_every_glass_opacity(self):
        """At 80% the panels' shade came to 264 of 255: Qt took the colour as invalid and drew them black."""
        import re
        from dockline import look as L
        for name in ('glass', 'glass_light'):
            for opacity in range(0, 101, 5):
                css = L.settings_css(L.LOOKS[name], opacity)
                for alpha in re.findall(r'rgba\(\d+,\s*\d+,\s*\d+,\s*(\d+)\)', css):
                    self.assertLessEqual(int(alpha), 255, (name, opacity))


class TrayButtonTests(unittest.TestCase):
    def test_hidden_icons_button_found_by_its_changing_name(self):
        """Windows build 26300 names it 'Show Hidden Icons Hide'; it was 'Show Hidden Icons'."""
        from dockline import uia
        listed = ['Touch Keyboard', 'Show Desktop', 'Volume Headphones (Protocol Max): 20%',
                  'Tray Input Indicator English (United States)']
        for name in ('Show Hidden Icons Hide', 'Show Hidden Icons', 'show hidden icons',
                     '显示隐藏的图标'):
            buttons = [(None, n, 'SystemTray.NormalButton') for n in listed + [name]]
            self.assertEqual(uia._choose(buttons)[1], name)
        self.assertIsNone(uia._choose([(None, n, 'SystemTray.NormalButton') for n in listed]))


class TrayOpeningTests(unittest.TestCase):
    def test_taskbar_brought_up_without_keys(self):
        """Keys an ordinary program sends are dropped while an administrator's window (Task Manager)
        is in front: the taskbar is made the active window instead, Win+B only if that is refused."""
        from unittest import mock
        from dockline import veil
        pressed, front = [], [0]
        with mock.patch.object(win.user32, 'FindWindowW', lambda cls, title: 77),                 mock.patch.object(win, 'activate', lambda hwnd: front.__setitem__(0, hwnd)),                 mock.patch.object(win.user32, 'GetForegroundWindow', lambda: front[0]),                 mock.patch.object(win, 'press', lambda *keys: pressed.append(keys)):
            veil.bring_up_taskbar()
            self.assertEqual(pressed, [])
            front[0] = 0                                                         # an administrator's window in front
            with mock.patch.object(win, 'activate', lambda hwnd: None):        # refused
                veil.bring_up_taskbar()
            self.assertEqual(pressed, [(win.VK_LWIN, ord('B'))])


    def test_start_opened_without_keys(self):
        """The Start button asks the taskbar to open Start (keys are dropped while an administrator's
        window is in front); the Windows key only when there is no taskbar to ask."""
        from unittest import mock
        posted, pressed, tray = [], [], [77]
        with mock.patch.object(win.user32, 'FindWindowW', lambda cls, title: tray[0]), \
                mock.patch.object(win.user32, 'PostMessageW', lambda *a: posted.append(a) or 1), \
                mock.patch.object(win, 'press', lambda *keys: pressed.append(keys)):
            from dockline.widgets import StartTile
            StartTile.click(None, None)
            self.assertEqual(posted, [(77, win.WM_SYSCOMMAND, win.SC_TASKLIST, 0)])
            self.assertEqual(pressed, [])
            tray[0] = 0
            win.open_start()
            self.assertEqual(pressed, [(win.VK_LWIN,)])

class WindowsTests(unittest.TestCase):
    """Reads only: nothing on screen changes."""

    def test_keyboards_read(self):
        layouts = win.keyboards()
        self.assertTrue(layouts)
        self.assertTrue(win.locale_of(layouts[0]))

    def test_only_windows_own_panels_over_the_dock_are_closed(self):
        """Start, Search and the like are found by the program that draws them, and only where they
        cover the window asked about; Esc goes only to that same window."""
        from unittest import mock
        windows = {
            1: ('C:\\Windows\\SystemApps\\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy\\'
                'StartMenuExperienceHost.exe', 'Windows.UI.Core.CoreWindow', (560, 200, 1360, 1030)),
            2: (r'C:\Riot Games\VALORANT\live\ShooterGame\Binaries\Win64\VALORANT-Win64-Shipping.exe',
                'VALORANTUnrealWindow', (0, 0, 1920, 1080)),
            3: (r'C:\Windows\explorer.exe', 'XamlExplorerHostIslandWindow', (0, 0, 1920, 1080)),
            4: (r'C:\Windows\explorer.exe', 'Shell_TrayWnd', (0, 1032, 1920, 1080)),
            5: (r'C:\Windows\SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\SearchHost.exe',
                'Windows.UI.Core.CoreWindow', (-1600, 100, -400, 900)),         # on the other screen
        }
        panel = (700, 850, 1160, 1020)
        front = [1]
        presses = []
        with mock.patch.object(win, 'foreground', lambda: front[0]), \
                mock.patch.object(win, 'window_pid', lambda hwnd: hwnd), \
                mock.patch.object(win, 'process_path', lambda pid: windows[pid][0]), \
                mock.patch.object(win, '_class_name', lambda hwnd: windows[hwnd][1]), \
                mock.patch.object(win, 'window_rect', lambda hwnd: panel if hwnd == 99 else windows[hwnd][2]), \
                mock.patch.object(win, 'press', lambda *keys: presses.append(keys)):
            found = {}
            for hwnd in windows:
                front[0] = hwnd
                found[hwnd] = win.shell_over(99)
            self.assertEqual(found, {1: 1, 2: 0, 3: 3, 4: 0, 5: 0})
            front[0] = 1
            self.assertTrue(win.dismiss_shell(1))
            front[0] = 2                                      # Start already gone: the game is in front
            self.assertFalse(win.dismiss_shell(1))
            self.assertEqual(presses, [(win.VK_ESCAPE,)])

    def test_maximised_window_is_not_full_screen(self):
        real_zoomed = win.user32.IsZoomed
        try:
            win.user32.IsZoomed = lambda hwnd: True
            self.assertFalse(win.full_screen_in_front(os.getpid() + 1))
        finally:
            win.user32.IsZoomed = real_zoomed


class NoticesTests(Temp):
    def store(self):
        """A stand-in for Windows' notification store, with its tables' real column names."""
        import sqlite3
        path = str(self.folder / 'wpndatabase.db')
        db = sqlite3.connect(path)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE Notification (Id INTEGER PRIMARY KEY, HandlerId INTEGER, Type TEXT, '
                   'Tag TEXT, "Group" TEXT)')
        db.execute('CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT)')
        db.execute('CREATE TABLE HandlerSettings (HandlerId INTEGER, SettingKey TEXT, Value INTEGER)')
        db.executemany('INSERT INTO NotificationHandler VALUES (?, ?)', [
            (1, 'Claude_pzs8sxrjxfjjc!Claude'), (2, 'Microsoft.ScreenSketch_8wekyb3d8bbwe!App'),
            (3, 'com.quiet.app'), (4, 'com.discord'),
            (5, 'Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneNotifications_com.instagram.android'),
            (6, 'MicrosoftWindows.CrossDevice_cw5n1h2txyewy!NewPhotoToast_1')])
        db.executemany('INSERT INTO HandlerSettings VALUES (?, ?, ?)', [
            (1, 's:banner', 1), (3, 's:banner', 0)])
        db.executemany('INSERT INTO Notification VALUES (?, ?, ?, ?, ?)', [
            (5, 1, 'toast', 'idle-a', 'session-a'), (6, 1, 'tile', '', ''), (7, 1, 'badge', '', '')])
        db.commit()
        return path, db

    def add(self, db, *row):
        db.execute('INSERT INTO Notification VALUES (?, ?, ?, ?, ?)', row)
        db.commit()

    def test_new_toasts_are_noticed_once(self):
        """What is there at start is no news; a new toast is, once; tiles, badges and removals are not;
        nor a toast replacing one already seen (Claude's per-session status at each turn's end), a
        screenshot confirmation, or an app whose banners are off in Windows Settings."""
        from dockline.notices import Notices
        path, db = self.store()
        notices = Notices(path=path)
        heard = []
        notices.arrived.connect(heard.append)
        notices.start()
        self.assertEqual((notices.newest, heard), (5, []))
        notices.look()
        self.assertEqual(heard, [])                                # nothing changed: not even read
        self.add(db, 8, 1, 'tile', '', '')
        notices.look()
        self.assertEqual(heard, [])
        db.execute('DELETE FROM Notification WHERE Id = 5')
        self.add(db, 9, 1, 'toast', 'idle-a', 'session-a')         # the same status, replaced
        notices.look()
        self.assertEqual(heard, [])
        self.add(db, 10, 2, 'toast', '', '')                       # "Screenshot copied to clipboard"
        self.add(db, 11, 3, 'toast', 't', '')                      # banners off for this app
        notices.look()
        self.assertEqual(heard, [])
        self.add(db, 14, 5, 'toast', 'p1', '')                     # the phone's, through Phone Link
        self.add(db, 15, 6, 'toast', 'p2', '')                     # a new photo on the phone
        notices.look()
        self.assertEqual(heard, [])
        self.add(db, 16, 4, 'toast', 'm1', '')                     # a message in an app on this PC
        self.add(db, 17, 1, 'toast', 'idle-b', 'session-b')        # another session's first
        notices.look()
        self.assertEqual(heard, ['com.discord'])                   # once a look, however many
        db.execute('DELETE FROM Notification WHERE Id = 17')       # dismissed: no news
        db.commit()
        notices.look()
        self.assertEqual((len(heard), notices.newest), (1, 17))
        notices.stop()
        db.close()

    def test_notifications_bring_the_dock_out_at_most_once_a_minute(self):
        from unittest import mock
        from dockline import dock as dock_module
        from dockline.dock import Dock
        cfg = Config(None)
        cfg['widgets'] = ['start', 'apps', 'clock']
        dock = Dock(cfg, testing=True)
        shown = []
        clock = [1000.0]
        with mock.patch.object(dock, 'alert', lambda **kw: shown.append(kw)), \
                mock.patch.object(dock_module.time, 'monotonic', lambda: clock[0]):
            dock.notice_arrived('a')
            dock.notice_arrived('b')
            clock[0] += dock_module.NOTICE_GAP + 1
            dock.notice_arrived('c')
        self.assertEqual(shown, [{'setting': 'alert_notices'}] * 2)
        dock.hide()

    def test_no_store_is_quiet(self):
        from dockline.notices import Notices
        notices = Notices(path=str(self.folder / 'missing.db'))
        notices.start()
        notices.look()
        self.assertIsNone(notices.newest)
        notices.stop()


class AIUsageTests(Temp):
    def write_claude(self, token: str, expires_in: float) -> Path:
        import json
        import time
        path = self.folder / '.claude' / '.credentials.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'claudeAiOauth': {
            'accessToken': token, 'refreshToken': 'r', 'subscriptionType': 'pro',
            'expiresAt': int((time.time() + expires_in) * 1000)}}), 'utf-8')
        return path

    def test_expired_claude_sign_in_is_renewed_by_the_cli(self):
        """Claude Code in the desktop app never renews ~/.claude/.credentials.json, so its token
        expires 8 hours after the CLI's last use: the CLI renews it, and the usage is read again."""
        from dockline import ai
        usage = {'five_hour': {'utilization': 12, 'resets_at': '2030-01-01T00:00:00Z'},
                 'seven_day': {'utilization': 45, 'resets_at': '2030-01-05T00:00:00Z'}}
        sent, renewals = [], []

        def opener(url, headers=None, **_):
            sent.append(headers['Authorization'])
            if headers['Authorization'] != 'Bearer new':
                raise ai._HttpStatus(401)
            return usage

        def renew(works=True):
            def go():
                renewals.append(1)
                if works:
                    self.write_claude('new', 8 * 3600)
                return works
            return go

        self.write_claude('old', -3600)                     # expired an hour ago
        state = ai.fetch_claude(self.folder, opener, renew())
        self.assertTrue(state['connected'])
        self.assertEqual((sent, len(renewals)), (['Bearer new'], 1))     # renewed before asking
        self.assertEqual([w['used'] for w in state['windows']], [12, 45])

        sent.clear(), renewals.clear()
        self.write_claude('old', 3600)                      # not expired by its clock, refused by the server
        state = ai.fetch_claude(self.folder, opener, renew())
        self.assertTrue(state['connected'])
        self.assertEqual((sent, len(renewals)), (['Bearer old', 'Bearer new'], 1))

        sent.clear(), renewals.clear()
        self.write_claude('old', -3600)                     # the CLI could not renew it (signed out)
        state = ai.fetch_claude(self.folder, opener, renew(False))
        self.assertFalse(state['connected'])
        self.assertTrue(state['signed_in'])
        self.assertEqual(state['error'], ai.CLAUDE_EXPIRED)
        self.assertEqual(len(renewals), 1)                  # tried once, not again on the refusal

    def test_claude_renewal_uses_claude_codes_refresh_grant_under_its_locks(self):
        """An expired sign-in renewed as Claude Code renews it: its refresh grant, its locks held while
        asking, the new tokens written back with the rest of the file kept; a refusal waits the gap,
        and a lock Claude Code holds now leaves the renewal to it."""
        import json
        import time
        from unittest import mock
        from dockline import ai
        path = self.write_claude('old', -3600)
        raw = json.loads(path.read_text('utf-8'))
        raw['claudeAiOauth'].update(refreshToken='r1', scopes=['user:inference', 'user:profile'],
                                    subscriptionType='pro', rateLimitTier='default_claude_ai')
        path.write_text(json.dumps(raw, separators=(',', ':')), 'utf-8')
        asked, agents = [], []
        answers = [ai._HttpStatus(400)]

        def post(url, method='GET', headers=None, body=None, timeout=8):
            asked.append((url, method, dict(body), [lock.is_dir() for lock in ai._claude_locks(path)]))
            agents.append(headers['User-Agent'])
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.dict(ai._claude_renewal, {'at': -1e9, 'mtime': -1.0}):
            self.assertFalse(ai._refresh_claude_cli_token(path, post))          # refused: signed out
            self.assertEqual(asked[0][:3], (ai.CLAUDE_TOKEN_URL, 'POST', {
                'grant_type': 'refresh_token', 'refresh_token': 'r1', 'client_id': ai.CLAUDE_CLIENT_ID,
                'scope': 'user:inference user:profile'}))
            self.assertEqual(asked[0][3], [True, True])                        # under Claude Code's locks
            # as Claude Code itself: the sign-in server answers any other client with 429
            self.assertRegex(agents[0], r'^claude-cli/\d+\.\d+\.\d+ \(external, cli\)$')
            self.assertEqual([lock.exists() for lock in ai._claude_locks(path)], [False, False])
            self.assertFalse(ai._refresh_claude_cli_token(path, post))          # too soon: not asked again
            self.assertEqual(len(asked), 1)

            _claude_lock = ai._claude_locks(path)[0]
            _claude_lock.mkdir()                                                # Claude Code renewing now
            os.utime(path, (1e9, 1e9))                                          # the file changed: try again
            self.assertFalse(ai._refresh_claude_cli_token(path, post))
            self.assertEqual(len(asked), 1)
            self.assertTrue(_claude_lock.is_dir())                              # its lock left alone
            os.utime(_claude_lock, (time.time() - 120, time.time() - 120))      # its holder ended long ago
            os.utime(path, (1e9 + 1, 1e9 + 1))
            answers.append({'access_token': 'new', 'refresh_token': 'r2', 'expires_in': 28800,
                            'scope': 'user:inference user:profile'})
            self.assertTrue(ai._refresh_claude_cli_token(path, post))
            oauth = json.loads(path.read_text('utf-8'))['claudeAiOauth']
            self.assertEqual((oauth['accessToken'], oauth['refreshToken']), ('new', 'r2'))
            self.assertAlmostEqual(oauth['expiresAt'] / 1000, time.time() + 28800, delta=5)
            self.assertEqual((oauth['subscriptionType'], oauth['rateLimitTier']), ('pro', 'default_claude_ai'))
            self.assertEqual([lock.exists() for lock in ai._claude_locks(path)], [False, False])
            self.assertFalse(path.with_name(path.name + '.dockline').exists())

    def test_expired_claude_sign_in_is_said_not_read_as_a_rate_limit(self):
        """Not renewed and expired: the card says so, with no request (the server answers it with 429).
        A reading kept through rate limits for long says why it is not newer."""
        import time
        from dockline import ai
        self.write_claude('old', -3600)
        asked = []
        state = ai.fetch_claude(self.folder, lambda *a, **k: asked.append(1), lambda: False)
        self.assertEqual((state['error'], asked), (ai.CLAUDE_EXPIRED, []))

        tracker = ai.AIUsage(Config(None), testing=True, home=self.folder)
        tracker.latest = {'claude': {'connected': True, 'signed_in': True, 'plan': 'Pro', 'error': '',
                                     'windows': [{'key': 'weekly', 'label': 'Weekly', 'used': 55.0,
                                                  'resets_at': None}],
                                     'updated_at': time.time() - 3600}}

        def limited(**_):
            raise ai._RateLimited(600)

        real = ai.FETCHERS['claude']
        ai.FETCHERS['claude'] = limited
        try:
            got = tracker._collect(force=True, providers=['claude'])
        finally:
            ai.FETCHERS['claude'] = real
        self.assertEqual(got['claude']['error'], ai.RATE_LIMITED)
        self.assertEqual(got['claude']['windows'][0]['used'], 55.0)

    def test_reply_cost_at_api_prices(self):
        """A reply priced as the API would bill it: input, output, both cache writes (1.25x and 2x the
        input price) and cache reads at the model's own rate; fast mode twice; unknown models none."""
        from dockline import ai
        usage = {'input_tokens': 1_000_000, 'output_tokens': 1_000_000, 'cache_read_input_tokens': 1_000_000,
                 'cache_creation_input_tokens': 2_000_000,
                 'cache_creation': {'ephemeral_5m_input_tokens': 1_000_000, 'ephemeral_1h_input_tokens': 1_000_000}}
        # Opus 5.5: $4 in, $20 out, $0.20 read, $5 5-minute write, $8 1-hour write
        self.assertAlmostEqual(ai.claude_reply_cost('claude-opus-5-5', usage), 4 + 20 + 0.2 + 5 + 8)
        self.assertAlmostEqual(ai.claude_reply_cost('claude-opus-5-5', {**usage, 'speed': 'fast'}), 2 * 37.2)
        self.assertAlmostEqual(ai.claude_reply_cost('claude-opus-5', usage), 5 + 25 + 0.5 + 6.25 + 10)
        self.assertAlmostEqual(ai.claude_reply_cost('claude-sonnet-5-5', {'input_tokens': 1_000_000,
                                                                          'cache_creation_input_tokens': 1_000_000}),
                               2 + 2.5)                                  # no split given: 5-minute writes
        self.assertIsNone(ai.claude_reply_cost('<synthetic>', usage))

    def test_claude_code_cost_by_plan_window(self):
        """Replies read from the transcripts once each (a streamed reply's last line counts), only what
        was added on later looks, and summed from each window's start (its reset less its length)."""
        import json
        import time
        from dockline import ai
        projects = self.folder / '.claude' / 'projects' / 'p'
        projects.mkdir(parents=True)
        log = projects / 's.jsonl'
        now = time.time()

        def reply(mid, hours_ago, out):
            return json.dumps({'type': 'assistant', 'timestamp': now - hours_ago * 3600,
                               'message': {'id': mid, 'model': 'claude-opus-5-5',
                                           'usage': {'input_tokens': 0, 'output_tokens': out}}}) + '\n'

        log.write_text(reply('a', 1, 10_000) + reply('a', 1, 50_000) + reply('b', 30, 100_000)
                       + reply('c', 300, 100_000) + '{"type": "user"}\n', 'utf-8')
        costs = ai.ClaudeCosts(self.folder / '.claude' / 'projects')
        windows = [{'key': 'session', 'resets_at': now + 3600}, {'key': 'weekly', 'resets_at': now + 86400}]
        self.assertEqual(costs.estimate(windows, now), {'session': 1.0, 'weekly': 3.0})   # $20 a million out
        with open(log, 'a', encoding='utf-8') as handle:
            handle.write(reply('d', 0.5, 50_000) + '{"partial')
        self.assertEqual(costs.estimate(windows, now), {'session': 2.0, 'weekly': 4.0})
        self.assertEqual(costs.read_to[str(log)][0], log.stat().st_size - len('{"partial'))

    def test_usage_credits_and_cost_lines(self):
        from dockline import ai, widgets
        spend = ai.parse_claude_spend({'spend': {'used': {'amount_minor': 1234, 'currency': 'USD', 'exponent': 2},
                                                 'limit': {'amount_minor': 5000, 'currency': 'USD', 'exponent': 2},
                                                 'enabled': True}})
        self.assertEqual(spend, {'used': 12.34, 'limit': 50.0, 'enabled': True, 'currency': 'USD'})
        off = ai.parse_claude_spend({'spend': {'used': {'amount_minor': 0, 'currency': 'USD', 'exponent': 2},
                                               'limit': None, 'enabled': False}})
        lines = widgets._cost_lines({'spend': off, 'estimate': {'session': 15.7382, 'weekly': 1218.7}})
        self.assertEqual(lines, ['Estimated API cost: $15.74 this session, $1,218.70 this week. '
                                 'Claude Code on this PC, at API prices.',
                                 'Usage credits: Off ($0.00 used this month)'])
        self.assertEqual(widgets._cost_lines({'spend': spend}), ['Usage credits: $12.34 of $50.00 used this month'])
        self.assertEqual(widgets._cost_lines({}), [])

    def test_parsers_and_reset_timers(self):
        import json
        import time
        from dockline import ai

        now = 1_800_000_000.0
        claude = ai.parse_claude_usage({
            'five_hour': {'utilization': 37.0, 'resets_at': '2027-01-15T06:00:00+00:00'},
            'seven_day': {'utilization': 12.0, 'resets_at': '2027-01-20T00:00:00Z'},
            'seven_day_sonnet': {'utilization': 5.0, 'resets_at': '2027-01-20T00:00:00Z'},
            'seven_day_opus': None,
        })
        self.assertEqual([w['label'] for w in claude], ['Session', 'Weekly', 'Sonnet'])
        self.assertEqual(ai.shown_value(claude[0], show_used=False, now=claude[0]['resets_at'] - 3600)[:1], (63,))
        self.assertEqual(ai.shown_value(claude[0], show_used=True, now=claude[0]['resets_at'] - 3600)[:1], (37,))
        # once the reset timestamp has passed, remaining allowance returns to 100%
        self.assertEqual(ai.shown_value(claude[0], show_used=False, now=claude[0]['resets_at'] + 5)[:1], (100,))

        self.assertEqual(ai.format_reset(now + 42 * 60, now=now), '42m')
        self.assertEqual(ai.format_reset(now + 2 * 3600 + 14 * 60, now=now), '2h 14m')
        self.assertEqual(ai.format_reset(now - 5, now=now), 'now')

        chatgpt, plan = ai.parse_chatgpt_usage({
            'plan_type': 'plus',
            'rate_limit': {
                'primary_window': {'used_percent': 25, 'reset_after_seconds': 5400},
                'secondary_window': {'used_percent': 40, 'reset_at': now + 3 * 86400},
            },
        }, now=now)
        self.assertEqual(plan, 'Plus')
        self.assertEqual([(w['label'], w['used']) for w in chatgpt], [('Session', 25.0), ('Weekly', 40.0)])

        gemini = ai.parse_gemini_quota({
            'buckets': [
                {'modelId': 'gemini-2.5-pro', 'remainingFraction': 0.82, 'resetTime': '2027-01-15T12:00:00Z'},
                {'modelId': 'gemini-3-pro', 'remainingFraction': 0.64, 'resetTime': '2027-01-15T12:00:00Z'},
                {'modelId': 'gemini-2.5-flash', 'remainingFraction': 0.91, 'resetTime': '2027-01-15T12:00:00Z'},
            ],
        })
        self.assertEqual([(w['label'], w['used']) for w in gemini], [('Pro', 36.0), ('Flash', 9.0)])

        sessions = self.folder / '.codex' / 'sessions' / '2026' / '11' / '17'
        sessions.mkdir(parents=True)
        rollout = sessions / 'rollout-1.jsonl'
        rollout.write_text(json.dumps({
            'timestamp': now,
            'type': 'event_msg',
            'payload': {
                'type': 'token_count',
                'rate_limits': {
                    'plan_type': 'pro',
                    'primary': {'used_percent': 19.0, 'resets_at': now + 7200},
                    'secondary': {'used_percent': 8.0, 'resets_at': now + 200000},
                },
            },
        }) + '\n', 'utf-8')
        local_wins, local_plan, _ = ai.latest_codex_session_limits(self.folder / '.codex' / 'sessions')
        self.assertEqual(local_plan, 'Pro')
        self.assertEqual(local_wins[0]['used'], 19.0)

    def test_fetch_cache_rate_limit_backoff_and_low_warning(self):
        import json
        import time
        from dockline import ai

        home = self.folder / 'home'
        (home / '.claude').mkdir(parents=True)
        secret_token = 'sk-ant-oat01-super-secret-never-persisted'
        (home / '.claude' / '.credentials.json').write_text(json.dumps({
            'claudeAiOauth': {'accessToken': secret_token, 'subscriptionType': 'max'}
        }), 'utf-8')
        (home / '.gemini').mkdir(parents=True)
        (home / '.gemini' / 'oauth_creds.json').write_text(json.dumps({
            'access_token': 'ya29.secret-gemini-token'
        }), 'utf-8')

        reset_future = time.time() + 3600

        def fake_opener(url, method='GET', headers=None, body=None, timeout=8):
            if 'anthropic.com' in url:
                return {'five_hour': {'utilization': 88.0, 'resets_at': reset_future},
                        'seven_day': {'utilization': 45.0, 'resets_at': reset_future + 86400}}
            if 'retrieveUserQuota' in url:
                return {'buckets': [{'modelId': 'gemini-2.5-pro', 'remainingFraction': 0.75,
                                     'resetTime': reset_future}]}
            return {}

        cfg = Config(None)
        coord = ai.AIUsage(cfg, testing=False, home=home, opener=fake_opener)
        alerts = []
        coord.low_allowance.connect(lambda p, lbl, left: alerts.append((p, lbl, round(left))))
        snap = coord.refresh_sync()

        self.assertTrue(snap['claude']['connected'])
        self.assertTrue(snap['gemini']['connected'])
        self.assertFalse(snap['chatgpt']['connected'])
        # Claude is at 88% used -> 12% left (<= 15%), so low_allowance fires once for this window
        self.assertEqual(alerts, [('Claude', 'Session', 12)])
        coord.refresh_sync()
        self.assertEqual(alerts, [('Claude', 'Session', 12)])     # never repeated for the same reset window

        # Cache file keeps usage numbers for fast startup, never any secret token
        cached_raw = coord.cache_path().read_text('utf-8')
        self.assertNotIn(secret_token, cached_raw)
        self.assertNotIn('ya29.', cached_raw)
        self.assertIn('"claude"', cached_raw)

        # HTTP 429 keeps the last good reading instead of blanking the dock tile
        def throttled_opener(url, method='GET', headers=None, body=None, timeout=8):
            raise ai._RateLimited()

        coord.opener = throttled_opener
        snap_after_429 = coord.refresh_sync(force=True)
        self.assertTrue(snap_after_429['claude']['connected'])
        self.assertEqual(snap_after_429['claude']['windows'][0]['used'], 88.0)

    def test_ai_tiles_single_provider_fallback_and_cards(self):
        import time
        from dockline.dock import Dock

        cfg = Config(None)
        cfg['widgets'] = ['ai_limits', 'claude', 'chatgpt', 'gemini']
        dock = Dock(cfg, testing=True)
        ai_tile = next(t for t in dock.tiles if t.ID == 'ai_limits')

        # With sample_snapshot (3 connected), AI Limits shows preferred pair (Claude + ChatGPT)
        rows = ai_tile.chosen_rows(ai_tile.snapshot())
        self.assertEqual([r[1] for r in rows], ['Claude', 'ChatGPT'])

        # When only one provider is signed in, AI Limits fills both rows with its Session & Weekly windows
        single_snap = {
            'claude': {
                'connected': True, 'signed_in': True, 'plan': 'Pro',
                'windows': [
                    {'key': 'session', 'label': 'Session', 'used': 20.0, 'resets_at': time.time() + 3600},
                    {'key': 'weekly', 'label': 'Weekly', 'used': 50.0, 'resets_at': time.time() + 86400},
                ],
                'updated_at': time.time(), 'error': '',
            },
            'chatgpt': {'connected': False, 'signed_in': False, 'plan': '', 'windows': [], 'updated_at': None, 'error': ''},
            'gemini': {'connected': False, 'signed_in': False, 'plan': '', 'windows': [], 'updated_at': None, 'error': ''},
        }
        rows_single = ai_tile.chosen_rows(single_snap)
        self.assertEqual([r[1] for r in rows_single], ['Session', 'Weekly'])

        # Every AI tile opens a card cleanly and updates Remaining/Used mode live
        ai_tiles = [t for t in dock.tiles if t.ID in ('ai_limits', 'claude', 'chatgpt', 'gemini')]
        self.assertEqual(len(ai_tiles), 4)
        for tile in ai_tiles:
            tile.click(tile.rect().center())
            self.assertIsNotNone(tile.card, tile.ID)
            tile.set_show_used(True)
            self.assertTrue(dock.cfg['ai_show_used'])
            tile.set_show_used(False)
            tile.card.close()
        dock.deleteLater()


    def test_gemini_windows_credential_manager_and_403_fallback(self):
        import json
        from dockline import ai

        utf8_blob = json.dumps({'token': {'access_token': 'cred-mgr-token', 'expiry': '2027-01-15T12:00:00-05:00'}}).encode('utf-8')
        utf16_blob = json.dumps({'access_token': 'keytar-token', 'expiry_date': 1800000000000}).encode('utf-16-le')
        self.assertEqual(ai._extract_gemini_token(ai._decode_cred_blob(utf8_blob))[0], 'cred-mgr-token')
        self.assertEqual(ai._extract_gemini_token(ai._decode_cred_blob(utf16_blob))[0], 'keytar-token')

        def fake_opener(url, method='GET', headers=None, body=None, timeout=8):
            ua = (headers or {}).get('User-Agent', '')
            if ua != 'antigravity/1.0':
                raise ai._HttpStatus(403, 'license check')
            return {
                'buckets': [
                    {'modelId': 'tab_flash_lite_preview', 'remainingFraction': 1.0},
                    {'modelId': 'gemini-2.5-pro', 'remainingFraction': 0.85, 'resetTime': '2027-01-15T12:00:00Z'},
                    {'modelId': 'gemini-3-flash', 'remainingFraction': 1.0, 'resetTime': '2027-01-15T12:00:00Z'},
                ]
            }

        empty_home = self.folder / 'no-files-home'
        empty_home.mkdir()
        state = ai.fetch_gemini(
            home=empty_home,
            opener=fake_opener,
            cred_reader=lambda: [('gemini-cli-oauth', 'keytar-token', None, 100.0)],
        )
        self.assertTrue(state['connected'])
        self.assertEqual([(w['label'], w['used'], w['resets_at'] is not None) for w in state['windows']],
                         [('Pro', 15.0, True), ('Flash', 0.0, True)])


    def test_ai_settings_dropdown_and_hiding_services(self):
        from PySide6.QtWidgets import QPushButton
        from dockline.dock import Dock

        cfg = Config(None)
        cfg['widgets'] = ['ai_limits']
        dock = Dock(cfg, testing=True)
        ai_tile = next(tile for tile in dock.tiles if tile.ID == 'ai_limits')
        ai_tile.click(ai_tile.rect().center())
        card = ai_tile.card
        toggle = next(b for b in card.findChildren(QPushButton) if 'Settings' in b.text())
        # Settings drawer starts collapsed
        svc_chatgpt = next(b for b in card.findChildren(QPushButton) if b.text() == 'ChatGPT')
        self.assertTrue(svc_chatgpt.parentWidget().isHidden())
        toggle.click()
        self.assertFalse(svc_chatgpt.parentWidget().isHidden())

        # Turning off ChatGPT removes it from enabled_providers, card rows, and bar rows
        svc_chatgpt.click()
        self.assertEqual(cfg['ai_enabled'], ['claude', 'gemini'])
        self.assertEqual([r[1] for r in ai_tile.chosen_rows(ai_tile.snapshot())], ['Claude', 'Gemini'])
        self.assertNotIn('chatgpt', {prov for prov, _, _ in ai_tile._card_rows})

        toggle.click()
        self.assertTrue(svc_chatgpt.parentWidget().isHidden())
        card.close()
        dock.deleteLater()

    def test_widget_resize_handles_and_reset(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from dockline.dock import Dock

        cfg = Config(None)
        cfg['widgets'] = ['start', 'weather', 'clock']
        dock = Dock(cfg, testing=True)
        dock.set_editing(True)
        start_tile = next(tile for tile in dock.tiles if tile.ID == 'start')
        weather_tile = next(tile for tile in dock.tiles if tile.ID == 'weather')
        self.assertFalse(start_tile.resizable())
        self.assertTrue(weather_tile.resizable())

        default_w = weather_tile.width()
        grip_pt = weather_tile.handle_rect().center()
        press = QMouseEvent(QEvent.MouseButtonPress, grip_pt, QPointF(200, 20), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        move = QMouseEvent(QEvent.MouseMove, grip_pt + QPointF(24, 0), QPointF(224, 20), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        release = QMouseEvent(QEvent.MouseButtonRelease, grip_pt + QPointF(24, 0), QPointF(224, 20), Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        weather_tile.mousePressEvent(press)
        weather_tile.mouseMoveEvent(move)
        weather_tile.mouseReleaseEvent(release)

        self.assertEqual(weather_tile.width(), default_w + 24)
        self.assertIn('weather', cfg['widget_widths'])

        # Rebuilding the dock preserves the saved custom width, and reset_width restores default
        dock.rebuild()
        weather_tile = next(tile for tile in dock.tiles if tile.ID == 'weather')
        self.assertEqual(weather_tile.width(), default_w + 24)
        weather_tile.reset_width()
        self.assertEqual(weather_tile.width(), default_w)
        self.assertNotIn('weather', cfg['widget_widths'])
        dock.deleteLater()


    def test_ai_card_stays_on_screen_when_toggling_services(self):
        """Showing or hiding an AI service inside an open card must grow/shrink upward from the dock
        rather than letting Qt push the bottom of the card below the dock or off the screen."""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QPushButton
        from dockline import motion
        from dockline.card import Card
        from dockline.dock import Dock

        real_motion = motion.on
        motion.on = lambda: False
        try:
            cfg = Config(None)
            cfg['ai_enabled'] = ['claude', 'gemini']
            cfg['widgets'] = ['ai_limits']
            dock = Dock(cfg, testing=True)
            dock.testing = False
            dock.ai.stop()
            dock.ai.latest['chatgpt'] = {'connected': False, 'signed_in': False, 'plan': '', 'windows': [],
                                         'updated_at': None, 'error': ''}
            screen = QGuiApplication.primaryScreen().geometry()
            dock_y = screen.bottom() - 58
            dock.setGeometry(QRect(screen.center().x() - 200, dock_y, 400, 52))
            dock.show()
            QApplication.processEvents()

            tile = next(t for t in dock.tiles if t.ID == 'ai_limits')
            tile.click(tile.rect().center())
            card = tile.card
            QApplication.processEvents()
            initial_bottom = card.geometry().bottom()
            self.assertLessEqual(initial_bottom, dock_y - 7)
            self.assertLessEqual(initial_bottom, screen.bottom())

            toggle = next(b for b in card.findChildren(QPushButton) if 'Settings' in b.text())
            toggle.click()
            QApplication.processEvents()
            self.assertEqual(card.geometry().bottom(), initial_bottom)

            # Toggle ChatGPT ON (adds wrapped multi-line hint + On bar row) and process deferred LayoutRequest
            chatgpt_btn = [b for b in card.findChildren(QPushButton) if b.text() == 'ChatGPT'][0]
            chatgpt_btn.click()
            QApplication.processEvents()
            self.assertLessEqual(card.geometry().bottom(), initial_bottom)
            self.assertGreaterEqual(card.geometry().top(), screen.top())

            # Toggle ChatGPT OFF again
            chatgpt_btn.click()
            QApplication.processEvents()
            self.assertEqual(card.geometry().bottom(), initial_bottom)

            from dockline.card import Card
            card.close()
            dock.stop()
            dock.testing = True
            dock.close()
            dock.deleteLater()
            QApplication.processEvents()
            QApplication.processEvents()
            Card.release_closed()
        finally:
            motion.on = real_motion


    def test_antigravity_quota_endpoint_and_ring_widget_styles(self):
        from PySide6.QtWidgets import QPushButton
        from dockline import ai
        from dockline.dock import Dock

        urls = []

        def fake_agy_opener(url, method='GET', headers=None, body=None, timeout=8):
            urls.append(url)
            if 'loadCodeAssist' in url:
                return {'cloudaicompanionProject': 'agy-proj-1'}
            if 'daily-cloudcode-pa.googleapis.com' in url:
                return {
                    'buckets': [
                        {'modelId': 'gemini-4-hydrogen-sonar', 'remainingFraction': 0.72, 'resetTime': '2027-01-15T12:00:00Z'},
                        {'modelId': 'gemini-3-flash', 'remainingFraction': 0.72, 'resetTime': '2027-01-15T12:00:00Z'},
                        {'modelId': 'claude-sonnet-4-6', 'remainingFraction': 0.90, 'resetTime': '2027-01-15T14:00:00Z'},
                    ]
                }
            return {'buckets': []}

        empty_home = self.folder / 'agy-home'
        empty_home.mkdir()
        state = ai.fetch_gemini(
            home=empty_home,
            opener=fake_agy_opener,
            cred_reader=lambda: [('gemini:antigravity', 'agy-tok', None, 100.0)],
        )
        self.assertTrue(state['connected'])
        self.assertEqual(state['plan'], 'Antigravity')
        self.assertTrue(any('daily-cloudcode-pa.googleapis.com' in u for u in urls))
        self.assertEqual([(w['label'], w['used']) for w in state['windows']],
                         [('Pro', 28.0), ('Flash', 28.0), ('Claude (AGY)', 10.0)])

        cfg = Config(None)
        cfg['widgets'] = ['ai_limits']
        dock = Dock(cfg, testing=True)
        tile = next(t for t in dock.tiles if t.ID == 'ai_limits')
        self.assertEqual(tile._tile_style(), 'rings')
        tile.grab()

        tile.click(tile.rect().center())
        card = tile.card
        toggle = next(b for b in card.findChildren(QPushButton) if 'Settings' in b.text())
        toggle.click()
        for style_word, expected in (('Gauges', 'gauges'), ('Bars', 'bars'), ('Rings', 'rings')):
            btn = next(b for b in card.findChildren(QPushButton) if b.text() == style_word)
            btn.click()
            self.assertEqual(cfg['ai_tile_style'], expected)
            tile.grab()
        card.close()
        dock.deleteLater()


    def test_usage_rings_stay_visible_as_tile_compacts(self):
        """Compacting empty space must never hide the usage status circles while shrinking the tile."""
        from PySide6.QtCore import QRectF
        from dockline import look as L
        from dockline.dock import Dock

        cfg = Config(None)
        cfg['widgets'] = ['ai_limits']
        dock = Dock(cfg, testing=True)
        tile = next(t for t in dock.tiles if t.ID == 'ai_limits')

        real_ring = L.ring
        for width in (210, 175, 136, 108, 78):
            tile.setFixedWidth(width)
            ring_calls = []

            def spy_ring(painter, rect, *rest, out=ring_calls):
                out.append(QRectF(rect))
                return real_ring(painter, rect, *rest)

            L.ring = spy_ring
            try:
                tile.grab()
            finally:
                L.ring = real_ring
            self.assertEqual(len(ring_calls), 2, f'expected 2 rings at width={width}, got {len(ring_calls)}')
        dock.deleteLater()

    def test_widget_resize_handles_and_reset(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from dockline.dock import Dock

        cfg = Config(None)
        cfg['widgets'] = ['start', 'weather', 'clock']
        dock = Dock(cfg, testing=True)
        dock.set_editing(True)
        start_tile = next(tile for tile in dock.tiles if tile.ID == 'start')
        weather_tile = next(tile for tile in dock.tiles if tile.ID == 'weather')
        self.assertFalse(start_tile.resizable())
        self.assertTrue(weather_tile.resizable())

        default_w = weather_tile.width()
        grip_pt = weather_tile.handle_rect().center()
        press = QMouseEvent(QEvent.MouseButtonPress, grip_pt, QPointF(200, 20), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        move = QMouseEvent(QEvent.MouseMove, grip_pt + QPointF(24, 0), QPointF(224, 20), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        release = QMouseEvent(QEvent.MouseButtonRelease, grip_pt + QPointF(24, 0), QPointF(224, 20), Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        weather_tile.mousePressEvent(press)
        weather_tile.mouseMoveEvent(move)
        weather_tile.mouseReleaseEvent(release)

        self.assertEqual(weather_tile.width(), default_w + 24)
        self.assertIn('weather', cfg['widget_widths'])

        # Rebuilding the dock preserves the saved custom width, and reset_width restores default
        dock.rebuild()
        weather_tile = next(tile for tile in dock.tiles if tile.ID == 'weather')
        self.assertEqual(weather_tile.width(), default_w + 24)
        weather_tile.reset_width()
        self.assertEqual(weather_tile.width(), default_w)
        self.assertNotIn('weather', cfg['widget_widths'])
        dock.deleteLater()


    def test_ai_card_stays_on_screen_when_toggling_services(self):
        """Showing or hiding an AI service inside an open card must grow/shrink upward from the dock
        rather than letting Qt push the bottom of the card below the dock or off the screen."""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QPushButton
        from dockline import motion
        from dockline.card import Card
        from dockline.dock import Dock

        real_motion = motion.on
        motion.on = lambda: False
        try:
            cfg = Config(None)
            cfg['ai_enabled'] = ['claude', 'gemini']
            cfg['widgets'] = ['ai_limits']
            dock = Dock(cfg, testing=True)
            dock.testing = False
            dock.ai.stop()
            dock.ai.latest['chatgpt'] = {'connected': False, 'signed_in': False, 'plan': '', 'windows': [],
                                         'updated_at': None, 'error': ''}
            screen = QGuiApplication.primaryScreen().geometry()
            dock_y = screen.bottom() - 58
            dock.setGeometry(QRect(screen.center().x() - 200, dock_y, 400, 52))
            dock.show()
            QApplication.processEvents()

            tile = next(t for t in dock.tiles if t.ID == 'ai_limits')
            tile.click(tile.rect().center())
            card = tile.card
            QApplication.processEvents()
            initial_bottom = card.geometry().bottom()
            self.assertLessEqual(initial_bottom, dock_y - 7)
            self.assertLessEqual(initial_bottom, screen.bottom())

            toggle = next(b for b in card.findChildren(QPushButton) if 'Settings' in b.text())
            toggle.click()
            QApplication.processEvents()
            self.assertEqual(card.geometry().bottom(), initial_bottom)

            # Toggle ChatGPT ON (adds wrapped multi-line hint + On bar row) and process deferred LayoutRequest
            chatgpt_btn = [b for b in card.findChildren(QPushButton) if b.text() == 'ChatGPT'][0]
            chatgpt_btn.click()
            QApplication.processEvents()
            self.assertLessEqual(card.geometry().bottom(), initial_bottom)
            self.assertGreaterEqual(card.geometry().top(), screen.top())

            # Toggle ChatGPT OFF again
            chatgpt_btn.click()
            QApplication.processEvents()
            self.assertEqual(card.geometry().bottom(), initial_bottom)

            card.close()
            dock.testing = True
            dock.close()
            dock.deleteLater()
            QApplication.processEvents()
        finally:
            motion.on = real_motion


    def test_antigravity_quota_endpoint_and_ring_widget_styles(self):
        from PySide6.QtWidgets import QPushButton
        from dockline import ai
        from dockline.dock import Dock

        urls = []

        def fake_agy_opener(url, method='GET', headers=None, body=None, timeout=8):
            urls.append(url)
            if 'loadCodeAssist' in url:
                return {'cloudaicompanionProject': 'agy-proj-1'}
            if 'daily-cloudcode-pa.googleapis.com' in url:
                return {
                    'buckets': [
                        {'modelId': 'gemini-4-hydrogen-sonar', 'remainingFraction': 0.72, 'resetTime': '2027-01-15T12:00:00Z'},
                        {'modelId': 'gemini-3-flash', 'remainingFraction': 0.72, 'resetTime': '2027-01-15T12:00:00Z'},
                        {'modelId': 'claude-sonnet-4-6', 'remainingFraction': 0.90, 'resetTime': '2027-01-15T14:00:00Z'},
                    ]
                }
            return {'buckets': []}

        empty_home = self.folder / 'agy-home'
        empty_home.mkdir()
        state = ai.fetch_gemini(
            home=empty_home,
            opener=fake_agy_opener,
            cred_reader=lambda: [('gemini:antigravity', 'agy-tok', None, 100.0)],
        )
        self.assertTrue(state['connected'])
        self.assertEqual(state['plan'], 'Antigravity')
        self.assertTrue(any('daily-cloudcode-pa.googleapis.com' in u for u in urls))
        self.assertEqual([(w['label'], w['used']) for w in state['windows']],
                         [('Pro', 28.0), ('Flash', 28.0), ('Claude (AGY)', 10.0)])

        cfg = Config(None)
        cfg['widgets'] = ['ai_limits']
        dock = Dock(cfg, testing=True)
        tile = next(t for t in dock.tiles if t.ID == 'ai_limits')
        self.assertEqual(tile._tile_style(), 'rings')
        tile.grab()

        tile.click(tile.rect().center())
        card = tile.card
        toggle = next(b for b in card.findChildren(QPushButton) if 'Settings' in b.text())
        toggle.click()
        for style_word, expected in (('Gauges', 'gauges'), ('Bars', 'bars'), ('Rings', 'rings')):
            btn = next(b for b in card.findChildren(QPushButton) if b.text() == style_word)
            btn.click()
            self.assertEqual(cfg['ai_tile_style'], expected)
            tile.grab()
        card.close()
        dock.deleteLater()

    def test_visible_dock_stays_centred_on_rebuild_and_window_open(self):
        """Rebuilding a visible dock (e.g. changing style/size in Settings) must never collapse resting
        width to 12 px, and new windows must not force an uncentred layout resize before fit() runs."""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication
        from dockline import motion, win
        from dockline.dock import Dock

        real_motion = motion.on
        real_windows = win.app_windows
        fake_windows = [win.Window(101, 'One', 1, r'C:\a\one.exe')]
        motion.on = lambda: False
        win.app_windows = lambda _pid=0: list(fake_windows)
        try:
            cfg = Config(None)
            cfg['autohide'] = True
            cfg['widgets'] = ['start', 'apps', 'ai_limits', 'clock']
            dock = Dock(cfg, testing=True)
            self.assertFalse(dock.isVisible())
            dock.testing = False
            dock.ai.stop()
            screen = QGuiApplication.primaryScreen().geometry()
            dock.show()
            dock.place()
            QApplication.processEvents()
            full_w = dock.resting.width()
            self.assertGreater(full_w, 200)
            self.assertEqual(dock.geometry(), dock.resting)

            # Changing look/size calls apply() -> rebuild() -> place() while dock is visible:
            # resting width must never collapse to 2 * L.GAP (12 px).
            cfg['look'] = 'midnight'
            dock.apply()
            self.assertGreater(dock.resting.width(), 200)
            self.assertEqual(dock.geometry(), dock.resting)
            QApplication.processEvents()
            self.assertEqual(dock.geometry(), dock.resting)

            # Opening new windows increases AppsTile.sizeHint; Qt's layout must NOT resize the top-level
            # Dock window at its old x before fit_later runs.
            before = QRect(dock.geometry())
            fake_windows.extend([
                win.Window(102, 'Two', 2, r'C:\b\two.exe'),
                win.Window(103, 'Three', 3, r'C:\c\three.exe'),
            ])
            apps = next(t for t in dock.tiles if t.ID == 'apps')
            apps.refresh()
            dock.fit_later.stop()
            QApplication.processEvents()
            self.assertEqual(dock.geometry(), before)

            # Once fit() runs, the dock widens and re-centres cleanly.
            dock.fit()
            self.assertGreater(dock.geometry().width(), before.width())
            self.assertEqual(dock.geometry(), dock.resting)
            self.assertAlmostEqual(dock.geometry().center().x(), dock.band.center().x(), delta=2)

            dock.testing = True
            dock.stop()
            dock.close()
            dock.deleteLater()
            QApplication.processEvents()
        finally:
            motion.on = real_motion
            win.app_windows = real_windows

    def test_open_cards_recentre_and_shrink_on_style_and_stopwatch_changes(self):
        """Switching AI widget style re-centres the open card over the resized tile, and Stopwatch
        laps/reset grow and shrink the open card upward from the dock."""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QPushButton
        from dockline import motion
        from dockline.card import Card
        from dockline.dock import Dock

        real_motion = motion.on
        motion.on = lambda: False
        try:
            cfg = Config(None)
            cfg['autohide'] = True
            cfg['widgets'] = ['ai_limits', 'stopwatch']
            dock = Dock(cfg, testing=True)
            dock.testing = False
            dock.ai.stop()
            screen = QGuiApplication.primaryScreen().geometry()
            dock.show()
            dock.place()
            QApplication.processEvents()

            ai_tile = next(t for t in dock.tiles if t.ID == 'ai_limits')
            ai_tile.click(ai_tile.rect().center())
            card = ai_tile.card
            QApplication.processEvents()
            toggle = next(b for b in card.findChildren(QPushButton) if 'Settings' in b.text())
            toggle.click()
            bars_btn = next(b for b in card.findChildren(QPushButton) if b.text() == 'Bars')
            bars_btn.click()
            QApplication.processEvents()
            tile_mid = ai_tile.mapToGlobal(ai_tile.rect().center()).x()
            self.assertAlmostEqual(card.geometry().center().x(), tile_mid, delta=3)
            card.close()
            QApplication.processEvents()

            Card.closed_at = 0.0
            sw_tile = next(t for t in dock.tiles if t.ID == 'stopwatch')
            sw_tile.click(sw_tile.rect().center())
            sw_card = sw_tile.card
            QApplication.processEvents()
            base_h = sw_card.height()
            base_bottom = sw_card.geometry().bottom()
            sw_tile.toggle()
            sw_tile.lap()
            sw_tile.lap()
            QApplication.processEvents()
            self.assertGreater(sw_card.height(), base_h)
            self.assertEqual(sw_card.geometry().bottom(), base_bottom)
            sw_tile.reset()
            QApplication.processEvents()
            self.assertEqual(sw_card.height(), base_h)
            self.assertEqual(sw_card.geometry().bottom(), base_bottom)

            sw_card.close()
            dock.testing = True
            dock.stop()
            dock.close()
            dock.deleteLater()
            QApplication.processEvents()
        finally:
            motion.on = real_motion

    def test_live_activity_watching_codex_instant_and_targeted_polling(self):
        import json
        import os
        import time
        from dockline import ai

        home = self.folder / 'live-home'
        (home / '.claude' / 'projects' / 'demo').mkdir(parents=True)
        (home / '.claude' / '.credentials.json').write_text(json.dumps({
            'claudeAiOauth': {'accessToken': 'claude-tok', 'subscriptionType': 'pro'}
        }), 'utf-8')
        (home / '.gemini' / 'antigravity-cli' / 'brain' / 'conv-1' / '.system_generated' / 'logs').mkdir(parents=True)
        (home / '.gemini' / 'oauth_creds.json').write_text(json.dumps({
            'access_token': 'gem-tok'
        }), 'utf-8')
        codex_day = home / '.codex' / 'sessions' / '2026' / '11' / '18'
        codex_day.mkdir(parents=True)

        claude_used = [20.0]
        gem_rem = [0.80]
        calls: list[str] = []
        reset_future = time.time() + 3600

        def fake_opener(url, method='GET', headers=None, body=None, timeout=8):
            if 'anthropic.com' in url:
                calls.append('claude')
                return {'five_hour': {'utilization': claude_used[0], 'resets_at': reset_future}}
            if 'retrieveUserQuota' in url:
                calls.append('gemini')
                return {'buckets': [{'modelId': 'gemini-2.5-pro', 'remainingFraction': gem_rem[0],
                                     'resetTime': reset_future}]}
            return {}

        cfg = Config(None)
        self.assertEqual(cfg['ai_refresh'], 'live')
        self.assertEqual(ai.poll_interval_for('live', card_open=False), 45)
        self.assertEqual(ai.poll_interval_for('live', card_open=True), 20)
        self.assertEqual(ai.poll_interval_for('30s', card_open=False), 30)
        self.assertEqual(ai.format_age(100.0, now=105.0), 'Updated just now')
        self.assertEqual(ai.format_age(100.0, now=123.0), 'Updated 20s ago')

        coord = ai.AIUsage(cfg, testing=False, home=home, opener=fake_opener)
        dispatched: list[tuple[set[str], float | None]] = []

        def sync_fetch(force=False, providers=None, now_mono=None):
            dispatched.append((set(providers or ()), now_mono))
            coord.refresh_sync(force=force, providers=providers, now_mono=now_mono)
            coord.changed.emit(dict(coord.latest))

        coord.fetch = sync_fetch
        coord.refresh_sync(now_mono=10.0)
        calls.clear()
        dispatched.clear()

        # 1. Local Codex rollout log updates ChatGPT allowance immediately with zero network traffic
        rollout = codex_day / 'rollout-live.jsonl'
        rollout.write_text(json.dumps({
            'timestamp': time.time(),
            'type': 'event_msg',
            'payload': {
                'type': 'token_count',
                'rate_limits': {
                    'plan_type': 'plus',
                    'primary': {'used_percent': 41.0, 'resets_at': reset_future},
                },
            },
        }) + '\n', 'utf-8')
        os.utime(rollout, (time.time() + 2, time.time() + 2))
        changed_events = []
        coord.changed.connect(lambda snap: changed_events.append(snap))
        coord.watch_files(now_mono=15.0)
        self.assertTrue(coord.latest['chatgpt']['connected'])
        self.assertEqual(coord.latest['chatgpt']['windows'][0]['used'], 41.0)
        self.assertGreaterEqual(len(changed_events), 1)
        self.assertEqual(calls, [])     # no Claude or Gemini HTTP requests made

        # 2. Claude Code turn writes a session JSONL -> debounced, then polls ONLY Claude (not Gemini)
        claude_used[0] = 35.0
        turn_file = home / '.claude' / 'projects' / 'demo' / 'session-1.jsonl'
        turn_file.write_text('{"type":"assistant"}\n', 'utf-8')
        os.utime(turn_file, (time.time() + 5, time.time() + 5))
        coord.watch_files(now_mono=24.0)
        self.assertEqual(calls, [])     # still debouncing at t=24.0 (0s quiet)
        coord.step(now_mono=26.0)       # 2.0s quiet (>= 1.5s debounce) and >= 12s since t=10.0 poll
        self.assertEqual(calls, ['claude'])
        self.assertEqual(coord.latest['claude']['windows'][0]['used'], 35.0)

        # 3. Antigravity CLI transcript update polls ONLY Gemini once quiet
        calls.clear()
        gem_rem[0] = 0.62
        agy_log = home / '.gemini' / 'antigravity-cli' / 'brain' / 'conv-1' / '.system_generated' / 'logs' / 'transcript.jsonl'
        agy_log.write_text('{"step_index":1}\n', 'utf-8')
        os.utime(agy_log, (time.time() + 10, time.time() + 10))
        coord.watch_files(now_mono=28.0)
        self.assertEqual(calls, [])
        coord.step(now_mono=30.0)
        self.assertEqual(calls, ['gemini'])
        self.assertEqual(coord.latest['gemini']['windows'][0]['used'], 38.0)

        # 4. Expired reset window triggers automatic rollover refresh
        calls.clear()
        claude_used[0] = 2.0
        coord.latest['claude']['windows'][0]['resets_at'] = time.time() - 1.0
        coord.step(now_mono=40.0)
        self.assertIn('claude', calls)
        self.assertEqual(coord.latest['claude']['windows'][0]['used'], 2.0)
        coord.stop()

    def test_updates_mode_selector_and_open_card_live_connection(self):
        import time
        from PySide6.QtWidgets import QApplication, QMenu, QPushButton
        from dockline import motion
        from dockline.dock import Dock

        real_motion = motion.on
        motion.on = lambda: False
        try:
            cfg = Config(None)
            cfg['widgets'] = ['ai_limits']
            dock = Dock(cfg, testing=True)
            tile = next(t for t in dock.tiles if t.ID == 'ai_limits')
            tile.click(tile.rect().center())
            card = tile.card
            self.assertIn('Live', tile._mode_sub.text())

            toggle = next(b for b in card.findChildren(QPushButton) if 'Settings' in b.text())
            toggle.click()
            for label, mode_key in (('30s', '30s'), ('1m', '1m'), ('5m', '5m'), ('Live', 'live')):
                btn = next(b for b in card.findChildren(QPushButton) if b.text() == label)
                btn.click()
                self.assertEqual(cfg['ai_refresh'], mode_key)

            # Right-click menu offers 'Live updates' shortcut when switched away from live
            tile.set_refresh_mode('5m')
            menu = QMenu()
            tile.menu(menu, tile.rect().center())
            actions = [a.text() for a in menu.actions() if a.text()]
            self.assertIn('Live updates', actions)
            next(a for a in menu.actions() if a.text() == 'Live updates').trigger()
            self.assertEqual(cfg['ai_refresh'], 'live')
            from dockline.card import Card
            card.close()
            Card.closed_at = 0.0

            # Dynamic card connection: opening card while unconnected then receiving live data populates rows
            dock.testing = False
            dock.ai.stop()
            dock.ai.testing = False
            dock.ai.latest = {
                p: {'connected': False, 'signed_in': False, 'plan': '', 'windows': [], 'updated_at': None, 'error': ''}
                for p in ('claude', 'chatgpt', 'gemini')
            }
            tile.click(tile.rect().center())
            card = tile.card
            self.assertIsNotNone(card)
            QApplication.processEvents()
            self.assertEqual(len(tile._card_rows), 0)

            dock.ai.latest['claude'] = {
                'connected': True, 'signed_in': True, 'plan': 'Pro',
                'windows': [{'key': 'session', 'label': 'Session', 'used': 24.0, 'resets_at': time.time() + 3600}],
                'updated_at': time.time(), 'error': '',
            }
            dock.ai.changed.emit(dict(dock.ai.latest))
            QApplication.processEvents()
            self.assertEqual(len(tile._card_rows), 1)

            card.close()
            dock.testing = True
            dock.close()
            dock.deleteLater()
            QApplication.processEvents()
        finally:
            motion.on = real_motion

    def test_gemini_weekly_summary_and_circular_card_gauges(self):
        from PySide6.QtCore import QRectF
        from dockline import ai, look as L
        from dockline.card import Card
        from dockline.dock import Dock

        seen_uas = []

        def fake_summary_opener(url, method='GET', headers=None, body=None, timeout=8):
            seen_uas.append((headers or {}).get('User-Agent', ''))
            if 'loadCodeAssist' in url:
                return {'cloudaicompanionProject': 'agy-proj-weekly'}
            if 'retrieveUserQuotaSummary' in url:
                return {
                    'groups': [
                        {
                            'displayName': 'Gemini Models',
                            'buckets': [
                                {'bucketId': 'gemini-weekly', 'window': 'weekly',
                                 'remainingFraction': 0.89, 'resetTime': '2027-01-22T00:00:00Z'},
                                {'bucketId': 'gemini-5h', 'window': '5h',
                                 'remainingFraction': 0.84, 'resetTime': '2027-01-15T10:00:00Z'},
                            ],
                        },
                        {
                            'displayName': 'Claude and GPT models',
                            'buckets': [
                                {'bucketId': '3p-weekly', 'window': 'weekly',
                                 'remainingFraction': 0.95, 'resetTime': '2027-01-22T00:00:00Z'},
                                {'bucketId': '3p-5h', 'window': '5h',
                                 'remainingFraction': 1.0, 'resetTime': '2027-01-15T12:00:00Z'},
                            ],
                        },
                    ]
                }
            return {}

        empty_home = self.folder / 'agy-weekly-home'
        empty_home.mkdir()
        state = ai.fetch_gemini(
            home=empty_home,
            opener=fake_summary_opener,
            cred_reader=lambda: [('gemini:antigravity', 'agy-weekly-tok', None, 100.0)],
        )
        self.assertTrue(state['connected'])
        self.assertEqual(state['plan'], 'Antigravity')
        self.assertTrue(any('hub/2.' in ua or 'acp/' in ua for ua in seen_uas))
        self.assertEqual(
            [(w['label'], w['used']) for w in state['windows']],
            [('Session', 16.0), ('Weekly', 11.0), ('Claude 5h', 0.0), ('Claude Wk', 5.0)],
        )

        # Open AI Limits card and verify each window renders as a circular progress ring in a horizontal grid
        Card.closed_at = 0.0
        cfg = Config(None)
        cfg['ai_enabled'] = ['claude', 'gemini']
        cfg['widgets'] = ['ai_limits']
        dock = Dock(cfg, testing=True)
        tile = next(t for t in dock.tiles if t.ID == 'ai_limits')
        tile.click(tile.rect().center())
        card = tile.card
        self.assertIsNotNone(card)
        # 2 Claude windows + 4 Gemini Antigravity windows = 6 circular ring cells
        self.assertEqual(len(tile._card_rows), 6)

        real_ring = L.ring
        drawn_rings = []

        def spy_ring(painter, rect, *rest):
            drawn_rings.append(QRectF(rect))
            return real_ring(painter, rect, *rest)

        L.ring = spy_ring
        try:
            card.grab()
        finally:
            L.ring = real_ring
        self.assertEqual(len(drawn_rings), 6)

        card.close()
        dock.deleteLater()


class AppIconTests(Temp):
    """Apps whose program file has no icon: Store apps and programs that set one only on their windows."""

    def setUp(self):
        super().setUp()
        self.patched(app_windows=lambda pid: [])               # no real windows in the strip

    make = DockTests.make
    apps = DockTests.apps

    def blank_program(self) -> str:
        path = self.folder / 'NoIcon.exe'
        path.write_bytes(b'MZ' + b'\0' * 510)                # a program file with no icon in it
        return str(path)

    def patched(self, **stand_ins):
        saved = {name: getattr(win, name) for name in stand_ins}
        for name, value in stand_ins.items():
            setattr(win, name, value)
        self.addCleanup(lambda: [setattr(win, name, value) for name, value in saved.items()])

    def test_program_files_with_and_without_icons(self):
        explorer = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'explorer.exe')
        self.assertTrue(win.has_own_icon(explorer))
        self.assertFalse(win.has_own_icon(self.blank_program()))
        self.assertTrue(win.has_own_icon(str(some_shortcut())))       # shortcuts are left to the shell
        self.assertEqual(win.package_app_id(os.getpid()), '')         # Python is not a packaged app

    def test_apps_folder_gives_a_packaged_apps_logo(self):
        found = win.app_id_image('windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel', 64)
        if found is None:
            self.skipTest('Settings is not in the Apps folder here')
        data, width, height = found
        self.assertGreaterEqual(width, 32)
        self.assertEqual(len(data), width * height * 4)
        self.assertTrue(any(data[3::4]), 'the logo is see-through everywhere')
        self.assertIsNone(win.app_id_image('No.Such.App_0000000000000!App', 64))

    def test_store_app_shows_its_package_logo(self):
        red = bytes([0, 0, 255, 255]) * 16                           # BGRA
        self.patched(app_id_of=lambda window: ('Some.App_x!App', True),
                     app_id_image=lambda app_id, size: (red, 4, 4))
        dock = self.make()
        tile = self.apps(dock)
        group = win.Group(r'C:\Program Files\WindowsApps\Some.App_x\App.exe', [win.Window(201, 'Store', 9, '')])
        pixmap = tile.icon_for(group)
        self.assertIsNotNone(pixmap)
        self.assertEqual(pixmap.toImage().pixelColor(1, 1).red(), 255)
        dock.deleteLater()

    def test_program_without_an_icon_shows_its_windows(self):
        from PySide6.QtGui import QColor, QImage
        image = QImage(16, 16, QImage.Format_ARGB32)
        image.fill(QColor(0, 200, 0))
        asked = []

        def window_icon(hwnd):
            asked.append(hwnd)
            return 0
        self.patched(app_id_of=lambda window: ('', False), window_icon=window_icon)
        dock = self.make()
        tile = self.apps(dock)
        blank = self.blank_program()
        pixmap = tile.icon_for(win.Group(blank, [win.Window(301, 'Settings', 9, blank)]))
        self.assertEqual(asked, [301], 'the window was not asked for its icon')
        self.assertIsNotNone(pixmap)                                  # the blank picture, for now
        self.assertIn(blank.lower(), tile.blank_icons)                # and it looks again later
        explorer = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'explorer.exe')
        asked.clear()
        self.assertIsNotNone(tile.icon_for(win.Group(explorer, [win.Window(302, 'Files', 9, explorer)])))
        self.assertEqual(asked, [], 'a program with its own icon shows that')
        dock.deleteLater()

    def test_icons_are_made_smaller_ahead_with_smooth_edges(self):
        from PySide6.QtGui import QColor, QPixmap
        dock = self.make()
        tile = self.apps(dock)
        big = QPixmap(256, 256)
        big.fill(QColor(200, 30, 30))
        small = tile.sized_icon(big, 40.4)
        self.assertEqual((small.width(), small.height()), (40, 40))
        self.assertIs(tile.sized_icon(big, 40), small)                 # kept, not made again
        self.assertIs(tile.sized_icon(big, 300), big)                   # never made bigger
        dock.deleteLater()

    def test_straight_alpha_logos_are_premultiplied(self):
        straight = bytes([200, 100, 50, 128, 0, 0, 0, 255, 255, 255, 255, 0])
        self.assertFalse(win._premultiplied(straight))
        self.assertEqual(list(win._premultiply(straight)), [100, 50, 25, 128, 0, 0, 0, 255, 0, 0, 0, 0])
        self.assertTrue(win._premultiplied(bytes([100, 50, 25, 128, 9, 9, 9, 255])))

    def test_only_small_icons_are_told_apart(self):
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
        from dockline import widgets

        def icon(side, filled):
            pixmap = QPixmap(side, side)
            pixmap.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pixmap)
            painter.fillRect(QRect(0, 0, filled, filled), QColor(20, 20, 20))
            painter.end()
            return QIcon(pixmap)
        self.assertTrue(widgets.small_only(icon(32, 32)))           # nothing bigger than 32
        self.assertTrue(widgets.small_only(icon(256, 48)))          # a 48 in the corner of a 256
        self.assertFalse(widgets.small_only(icon(256, 220)))        # a real large icon

    def test_placeholder_program_icon_gives_way_to_the_windows_own(self):
        import ctypes
        from dockline import widgets
        handle = ctypes.windll.user32.LoadIconW(None, ctypes.c_void_p(32516))     # IDI_INFORMATION
        self.patched(app_id_of=lambda window: ('', False), window_set_icon=lambda hwnd: handle)
        real_small_only = widgets.small_only
        widgets.small_only = lambda icon: True
        self.addCleanup(setattr, widgets, 'small_only', real_small_only)
        dock = self.make()
        tile = self.apps(dock)
        explorer = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'explorer.exe')
        pixmap = tile.icon_for(win.Group(explorer, [win.Window(401, 'Ask', 9, explorer)]))
        from PySide6.QtGui import QImage
        self.assertEqual(pixmap.toImage(), QImage.fromHICON(handle))
        dock.deleteLater()


if __name__ == '__main__':
    unittest.main()
