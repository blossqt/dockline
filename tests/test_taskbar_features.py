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


class AIUsageTests(Temp):
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


if __name__ == '__main__':
    unittest.main()
