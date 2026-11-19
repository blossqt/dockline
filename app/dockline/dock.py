"""The dock: a bar along the bottom (or top) of the main screen holding the widgets."""

from __future__ import annotations

import ctypes
import os
import sys
import time
import winreg
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QCursor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QWidget

from . import look as L
from . import motion
from . import suitetheme as theme
from . import uia, win
from .taskbar import Taskbar
from .ai import AIUsage
from .weather import Weather
from .notices import Notices
from .config import NEW_WIDGETS, ONLY_WITH_TWO_KEYBOARDS
from .widgets import REGISTRY

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
ALERT_HOLD = 1.2      # s an alert keeps a hidden dock out, once it is in
ALERT_SLIDE = 0.22    # s the dock takes to come in
WM_QUERYENDSESSION, WM_DISPLAYCHANGE = 0x11, 0x7E


def start_command() -> str:
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'main.py')
    return f'"{pythonw}" "{main}"'


def set_startup(on: bool) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, 'Dockline', 0, winreg.REG_SZ, start_command())
            else:
                try:
                    winreg.DeleteValue(key, 'Dockline')
                except FileNotFoundError:
                    pass
    except OSError:
        pass


def menu_css(look: L.Look, fill=None) -> str:
    return f"""
    QMenu {{ background: {L.rgba(fill if fill is not None else look.panel)}; border: 1px solid {L.rgba(look.edge)}; border-radius: 10px;
             padding: 6px; font-family: 'Segoe UI Variable Text'; font-size: 14px; }}
    QMenu::item {{ color: {look.text}; padding: 7px 28px 7px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {L.chip(look).name()}; color: {L.on_chip(look).name()}; }}
    QMenu::separator {{ height: 1px; background: {L.rgba(look.edge)}; margin: 5px 8px; }}
    QMenu::indicator {{ width: 0; }}
    """


class GlassMenu(QMenu):
    """The dock's right-click menu. In the Glass style, the screen behind it blurred under its tint
    (Windows' own blur comes out flat grey here: look.frosted_behind)."""

    RADIUS = 10

    def __init__(self, parent, glass: bool):
        super().__init__(parent)
        self.glass = glass
        self.backdrop = None

    def showEvent(self, event):
        if self.glass:                                   # before it is on screen: never in its own picture
            self.backdrop = L.frosted_behind(self.geometry())
        super().showEvent(event)

    def paintEvent(self, event):
        if self.glass and self.backdrop is not None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            L.paint_backdrop(painter, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.backdrop)
            painter.end()
        super().paintEvent(event)


SECOND_WIDGETS = ['start', 'apps', 'clock']     # a dock on another display, as Windows' second taskbars


class Dock(QWidget):
    def __init__(self, cfg, testing: bool = False, screen=None, main: bool = True):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('Dockline')
        self.setWindowIcon(L.app_icon())
        self.cfg = cfg
        self.testing = testing
        self.main = main                      # the main display's dock: widgets, Settings, the taskbar
        self.target_screen = screen or QGuiApplication.primaryScreen()
        self._screen_connected = False
        self.multi = False                    # docks on more than one display
        self.others: list = []                # the other displays' docks (on the main one)
        self.tucked = False                   # auto-hidden, beyond the screen's edge
        self.away_since: float | None = None
        self.peek = QTimer(self)              # auto-hide: where the pointer is
        self.peek.setInterval(60)
        self.peek.timeout.connect(self.watch_pointer)
        self.editing = False
        self.dropping = False                 # files are dragged over the apps
        self.appbar: win.AppBar | None = None
        self.taskbar = Taskbar(cfg)
        self.weather = Weather()
        self.ai = AIUsage(cfg, testing=testing)
        self.ai.low_allowance.connect(self._on_low_allowance)
        self.notices = Notices(self)              # Windows notifications arriving (main dock only)
        self.notices.arrived.connect(self.notice_arrived)
        self.tiles = []
        self.picker = None
        self.settings_window = None
        self.veil = None
        self.full_screen = False
        self._drag_from: QPoint | None = None
        self.moving = False
        self.sliding = False          # coming in or going out: place() leaves the position alone
        self.resting = QRect()        # where the dock sits when in place
        self.drag_offset = 0
        self.rest: dict = {}           # each widget's place in the row while one is dragged
        self.row = QHBoxLayout(self)
        self.row.setSizeConstraint(QHBoxLayout.SizeConstraint.SetNoConstraint)
        self.row.setSpacing(L.GAP)
        self.row.setContentsMargins(L.GAP, L.GAP, L.GAP, L.GAP)
        self.ticker = QTimer(self)
        self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self.tick)
        self.keeper = QTimer(self)
        self.keeper.setInterval(100)          # a taskbar that pops up is hidden again within a tenth of a second
        self.keeper.timeout.connect(self.taskbar.keep_hidden)
        self.place_soon = QTimer(self)
        self.place_soon.setSingleShot(True)
        self.place_soon.setInterval(150)
        self.place_soon.timeout.connect(self.place)
        self.band = QRect()                   # the screen band reserved for the dock
        self._fit = None
        self.fit_later = QTimer(self)         # widgets or apps came or went: grow or shrink to fit
        self.fit_later.setSingleShot(True)
        self.fit_later.setInterval(60)
        self.fit_later.timeout.connect(self.fit)
        self._slide = None
        self.full_watch = QTimer(self)        # while hidden for a full-screen app: is it still there?
        self.full_watch.setInterval(1000)
        self.full_watch.timeout.connect(self.check_full_screen)
        self.full_later = QTimer(self)        # a full-screen app in front while previews are open: looked at again
        self.full_later.setSingleShot(True)
        self.full_later.setInterval(300)
        self.full_later.timeout.connect(self.check_full_screen)
        if main:
            self.offer_new_widgets()
            if not testing and not cfg['pins_imported']:
                self.import_taskbar_pins()
        self.apply()

    # ------------------------------------------------------------ state

    def offer_new_widgets(self) -> None:
        """A version that brings a widget adds it to an existing dock once; removed, it stays removed."""
        changed = False
        for key, before in NEW_WIDGETS.items():
            if key in self.cfg['offered']:
                continue
            self.cfg['offered'] = self.cfg['offered'] + [key]
            if key in ONLY_WITH_TWO_KEYBOARDS and (self.testing or len(win.keyboards()) < 2):
                changed = True
                continue
            if key not in self.cfg['widgets']:
                widgets = list(self.cfg['widgets'])
                widgets.insert(widgets.index(before) if before in widgets else len(widgets), key)
                self.cfg['widgets'] = widgets
            changed = True
        if changed and not self.testing:
            self.cfg.save()

    def import_taskbar_pins(self) -> int:
        """Bring the Windows taskbar's pinned apps onto the dock (once by itself, and from Settings):
        each shortcut is copied into Dockline's folder and pinned after those already there."""
        from . import shortcuts
        pinned = list(self.cfg['pinned'])
        known = {shortcuts.program_of(p) or p.lower() for p in pinned}
        added = 0
        for path in shortcuts.taskbar_pins():
            program = shortcuts.program_of(path) or path.lower()
            if program in known or any(os.path.basename(p).lower() == os.path.basename(path).lower() for p in pinned):
                continue
            pinned.append(shortcuts.keep(path))
            known.add(program)
            added += 1
        self.cfg['pinned'] = pinned
        self.cfg['pins_imported'] = True
        if not self.testing:
            self.cfg.save()
        for tile in self.tiles:
            if tile.ID == 'apps':
                tile.refresh()
        return added

    @property
    def look(self) -> L.Look:
        name = self.cfg['look'] if self.cfg['look'] in L.STYLES else 'glass'
        if name == 'glass' and not self.glass_on():
            return L.LOOKS['black' if theme.is_dark() or self.testing else 'white']
        if name == 'glass' and not theme.is_dark():
            return L.LOOKS['glass_light']
        return L.LOOKS[name]

    def glass_on(self) -> bool:
        """The suite's glass is available here (Windows 11 22H2, transparency effects on)."""
        return not self.testing and theme.glass_on()

    @property
    def edge(self) -> str:
        return self.cfg['edge']

    @property
    def bar_height(self) -> int:
        return L.SIZES.get(self.cfg['size'], L.SIZES['M'])

    @property
    def tile_height(self) -> int:
        return self.bar_height - 2 * L.GAP

    def apply(self) -> None:
        """Take up the settings: look, size, widgets, place."""
        look = self.look
        L.HALO = QColor(0, 0, 0, 110) if look.glass and look.dark else None   # a soft shadow under light text
        self.setFixedHeight(self.bar_height)
        self.rebuild()
        self.glass()
        self.place()
        self.update()

    def rebuild(self) -> None:
        while self.row.count():
            item = self.row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if hasattr(widget, 'retire'):
                    widget.retire()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        keys = self.cfg['widgets'] if self.main else SECOND_WIDGETS
        self.tiles = [REGISTRY[key](self) for key in keys if key in REGISTRY]
        for tile in self.tiles:
            self.row.addWidget(tile, 1 if tile.STRETCH else 0)
            tile.setVisible(True)
        self.relayout()
        self.fit_soon()
        if self.picker is not None:
            self.picker.refresh()

    def tick(self) -> None:
        if self.full_screen:
            return
        for tile in self.tiles:
            try:
                tile.tick()
            except Exception as error:      # one widget's failure must not stop the others
                print(f'{tile.ID}: {error}', file=sys.stderr)

    # ------------------------------------------------------------ on screen

    def start(self) -> None:
        if self.main:
            self.taskbar.recover()
        self.show()
        hwnd = int(self.winId())
        win.no_activate(hwnd)
        self.glass()
        self.place()
        self.slide(True)
        if self.main and self.cfg['hide_taskbar']:
            self.taskbar.hide()
            self.place_soon.start()
        self.ticker.start()
        if self.main:
            self.keeper.start()
            self.start_guard()
            self.notices.start()
        win.user32.RegisterShellHookWindow(hwnd)     # the taskbar's notices: windows coming, going, flashing
        self.target_screen.geometryChanged.connect(self.screen_changed)
        self._screen_connected = True
        self.peek.start()
        if self.autohide():                          # shown for a moment at start, then tucked away
            self.away_since = time.monotonic() + 1.5

    def stop(self) -> None:
        """Give the screen and the taskbar back."""
        self.ai.stop()
        self.notices.stop()
        self.ticker.stop()
        self.keeper.stop()
        self.peek.stop()
        self.full_watch.stop()
        self.place_soon.stop()
        self.fit_later.stop()
        if self._screen_connected and self.screen_alive():
            try:
                self.target_screen.geometryChanged.disconnect(self.screen_changed)
            except (RuntimeError, TypeError):
                pass
            self._screen_connected = False
        if self.appbar is not None:
            self.appbar.remove()
        if not self.testing:
            win.user32.DeregisterShellHookWindow(int(self.winId()))
        if self.main and (self.cfg['hide_taskbar'] or self.cfg['taskbar_saved'] is not None):
            self.taskbar.restore()

    def glass(self) -> None:
        """Only the Glass style takes shots of the screen; the solid ones paint themselves."""
        if self.testing:
            return
        if self.isVisible():
            win.round_corners(int(self.winId()), False)   # the dock draws its own corners

    def place(self) -> None:
        if self.testing or self.moving or (not self.isVisible() and not self.tucked) or not self.screen_alive():
            return
        self._device = ''
        screen = self.target_screen
        ratio = screen.devicePixelRatio()
        g = screen.geometry()
        thickness = self.bar_height + 2 * L.MARGIN
        if self.autohide():                          # no screen space kept for it, as an auto-hidden taskbar
            if self.appbar is not None:
                self.appbar.remove()
            room = screen.availableGeometry()        # above the Windows taskbar when it shows
            top = room.top() if self.edge == 'top' else room.bottom() + 1 - thickness
            self.band = QRect(g.left(), top, g.width(), thickness)
            self.resting = self.compact(self.band)
            if not self.sliding and not self.tucked and self._fit is None and self.geometry() != self.resting:
                self.setGeometry(self.resting)
            return
        if self.appbar is None:
            self.appbar = win.AppBar(int(self.winId()))
        physical = (round(g.left() * ratio), round(g.top() * ratio), round((g.left() + g.width()) * ratio),
                    round((g.top() + g.height()) * ratio))
        edge = win.ABE_TOP if self.edge == 'top' else win.ABE_BOTTOM
        left, top, right, bottom = self.appbar.place(edge, physical, round(thickness * ratio))
        band = QRect(round(left / ratio), round(top / ratio), round((right - left) / ratio),
                     round((bottom - top) / ratio))
        self.band = band
        target = self.compact(band)
        self.resting = target
        if target != self.geometry() and not self.sliding and self._fit is None:
            self.setGeometry(target)

    def compact(self, band: QRect) -> QRect:
        """A bar in the middle of the band, as wide as its widgets and apps, at most the band."""
        room = band.width() - 2 * L.MARGIN
        width = min(self.row.sizeHint().width(), room)
        return QRect(band.center().x() - width // 2 + 1, band.y() + L.MARGIN, width, self.bar_height)

    def fit_soon(self) -> None:
        self.fit_later.start()

    def fit_now(self) -> None:
        """Resize and re-centre the dock immediately while a widget handle is dragged."""
        self.relayout()
        if self.testing:
            self.resize(self.row.sizeHint().width(), self.bar_height)
            return
        if self.band.isNull() or self.moving:
            return
        if self._fit is not None:
            try:
                self._fit.stop()
            except RuntimeError:
                pass
            self._fit = None
        target = self.compact(self.band)
        self.resting = target
        if self.isVisible() and not self.sliding:
            self.setGeometry(target)

    def fit(self) -> None:
        """Grow or shrink to the widgets and apps it holds, on the curve, staying centred."""
        if self.testing:
            self.resize(self.row.sizeHint().width(), self.bar_height)
            return
        if self.band.isNull() or self.moving:
            return
        target = self.compact(self.band)
        self.resting = target
        if self.sliding or not self.isVisible():
            return
        start = self.geometry()
        if start == target:
            return
        if self._fit is not None:
            try:
                self._fit.stop()
            except RuntimeError:
                pass

        def step(progress):
            self.setGeometry(QRect(round(start.x() + (target.x() - start.x()) * progress), target.y(),
                                   round(start.width() + (target.width() - start.width()) * progress),
                                   target.height()))

        def done():
            self._fit = None
            self.setGeometry(self.resting)

        self._fit = motion.run(self, motion.OPEN_MS, step, done)

    def glass_fill(self) -> QColor:
        """The Glass style's tint: as clear as Settings' Glass opacity says."""
        look = self.look
        if not look.glass:
            return look.c(look.bar)
        share = max(0, min(100, int(self.cfg.get('glass_opacity', 18)))) / 100
        tint = QColor(16, 16, 22) if look.dark else QColor(255, 255, 255)
        tint.setAlpha(max(2, round(255 * share)))      # never quite 0: clicks would go through
        return tint

    def open_tray(self) -> None:
        """Windows' own panel of the notification area's hidden icons (background apps). With the
        taskbar showing, its button is pressed. With it hidden, the taskbar has to come up for the
        panel to fill in, so it comes up behind a picture of the screen (veil.Veil): only the panel
        shows."""
        if self.testing:
            return
        from .veil import Veil, panel_open
        if panel_open():                             # open: the same button closes it
            self.close_tray()
            return
        if getattr(self, 'veil', None) is not None:
            return
        # with the taskbar showing, its button is there to press; either way the panel opens over the
        # dock's Tray button rather than by the clock
        self.veil = Veil(self, taskbar_up=win.taskbar_visible())
        self.veil.start()

    def close_tray(self) -> None:
        """Close the hidden-icons panel. A click on the dock does not take the keyboard from it (the
        dock never takes it), so it does not close by itself as on a click elsewhere: Escape, which
        it answers, is given to it - in front first, if it is not."""
        from .veil import panel_window
        panel = panel_window()
        if not panel:
            return
        if win.user32.GetForegroundWindow() != panel:
            win.activate(panel)
        if win.user32.GetForegroundWindow() == panel:
            win.press(0x1B)
        else:
            win.user32.ShowWindow(panel, 0)              # it would not come forward: hidden instead
        if getattr(self, 'veil', None) is not None:
            QTimer.singleShot(150, lambda: self.veil and self.veil.step())

    def press_tray(self) -> None:
        if uia.open_hidden_icons():
            return
        self._tray_tries += 1
        if self._tray_tries < 15:                        # about 1.5 s; it took 0.3 s here
            QTimer.singleShot(100, self.press_tray)
        elif win.user32.GetForegroundWindow() and \
                win._class_name(win.user32.GetForegroundWindow()) == 'Shell_TrayWnd':
            win.press(0x1B)                              # not found: give the keyboard back

    def panel_fill(self) -> QColor:
        """Cards and menus: the dock's own tint, a touch more of it for what is read."""
        fill = self.glass_fill()
        if self.look.glass:
            fill.setAlpha(min(255, fill.alpha() + 25))
        return fill

    def slide(self, coming: bool, then=None) -> None:
        """Come in from beyond the screen's edge, or go out past it, on the original's curve:
        220 ms in, 180 ms out."""
        if self._slide is not None:                    # a slide still running stops where it is:
            try:                                       # two at once ended with the dock off screen
                self._slide.stop()
            except RuntimeError:
                pass
            self._slide = None
        rest = self.resting if not self.resting.isNull() else self.geometry()
        away = (self.height() + 2 * L.MARGIN + 6) * (-1 if self.edge == 'top' else 1)
        now = self.y() - rest.y() if self.isVisible() else away
        start, end = (now if not coming or self.isVisible() else away, 0) if coming else (now, away)
        if coming:
            self.setGeometry(rest.translated(0, start))
            self.show()
        self.sliding = True

        def step(progress):
            self.move(rest.x(), rest.y() + round(start + (end - start) * progress))

        def done():
            self._slide = None
            self.sliding = False
            if coming:
                self.place()
                if self.geometry() != self.resting and not self.resting.isNull():
                    self.setGeometry(self.resting)
            if then:
                then()

        self._slide = motion.run(self, motion.OPEN_MS if coming else motion.CLOSE_MS, step, done)

    # ------------------------------------------------------------ full-screen apps

    def check_full_screen(self) -> None:
        """Hide for a full-screen app on the dock's screen, and come back once it is gone. Windows'
        own notice is only a hint: it also fires for the desktop itself, which hid the dock for good."""
        full = win.full_screen_in_front(os.getpid(), self.device or None)
        if full and not self.full_screen and self.previews_open():
            self.full_later.start()              # Start closed for the previews: the dock goes once they close
            return
        if full and not self.full_screen:
            self.full_screen = True
            self.full_watch.start()
            self.slide(False, lambda: self.full_screen and self.hide())
        elif not full and self.full_screen:
            self.full_screen = False
            self.full_watch.stop()
            self.glass()
            self.slide(True)
            self._poke_ai()

    def previews_open(self) -> bool:
        return any(tile.ID == 'apps' and tile.previews_visible() for tile in self.tiles)

    def shell_notice(self, code: int, hwnd: int) -> None:
        apps = next((t for t in self.tiles if t.ID == 'apps'), None)
        if apps is None:
            return
        if code == win.HSHELL_FLASH:
            if apps.flash(hwnd, True) and (self.testing or self.shows_window(hwnd)):
                self.alert()
        elif code in (win.HSHELL_WINDOWACTIVATED, win.HSHELL_RUDEAPPACTIVATED):
            apps.flash(hwnd, False)
            apps.window_changed()
        elif code in (win.HSHELL_WINDOWCREATED, win.HSHELL_WINDOWDESTROYED, win.HSHELL_REDRAW):
            apps.window_changed()

    def _poke_ai(self, max_age: float = 20.0) -> None:
        if self.main and not self.testing and any(t.GROUP == 'AI' for t in self.tiles):
            self.ai.poke(max_age=max_age)

    def _on_low_allowance(self, _provider: str, _label: str, _left: float) -> None:
        """An active AI allowance dropped to 15% left or less: bring a tucked dock out briefly."""
        if self.main and not self.testing:
            self.alert(4.0)

    def alert(self, hold: float = ALERT_HOLD) -> None:
        """Something wants to be seen - an app requesting attention, a timer ending, a notification:
        a hidden dock comes out for a moment and goes again, unless the pointer reaches it. Never over
        a full-screen app or game."""
        if not self.cfg.get('alerts', True) or not self.autohide() or self.full_screen \
                or not self.screen_alive():
            return
        until = time.monotonic() + ALERT_SLIDE + hold
        if self.tucked:
            if not self.testing and win.full_screen_in_front(os.getpid(), self.device or None):
                return
            self.place()
            self.tucked = False
            self.away_since = until
            self.slide(True)
        elif self.away_since is not None:            # out, and about to go: it stays for this too
            self.away_since = max(self.away_since, until)

    def notice_arrived(self) -> None:
        """A Windows notification arrived."""
        self.alert()

    def come_back(self) -> None:
        """Dockline was started again: whatever happened, the dock shows."""
        self.full_screen = False
        self.full_watch.stop()
        self.tucked = False
        self.away_since = time.monotonic() + 2 if self.autohide() else None
        if not self.isVisible() or self.geometry() != self.resting:
            self.slide(True)
        self._poke_ai()
        for other in self.others:
            other.come_back()

    # ------------------------------------------------------------ auto-hide

    def autohide(self) -> bool:
        return bool(self.cfg.get('autohide', True)) and not self.testing

    def tray_open(self) -> bool:
        """Windows' hidden-icons panel (the Tray widget's) is open: the dock stays while the pointer is
        in it - or anywhere, until it closes on a click elsewhere. Looked for only once the pointer has
        left the dock, so not every 60 ms while it rests on it."""
        if self.testing or not self.main:
            return False
        from .veil import panel_window
        return bool(panel_window())

    def busy(self) -> bool:
        """Something of the dock's is open or held: it stays out."""
        if self.moving or self.editing or self.sliding or self.dropping or QApplication.activePopupWidget() is not None:
            return True
        if self.settings_window is not None and self.settings_window.isVisible():
            return True                              # Settings open: the dock stays out to be seen
        for tile in self.tiles:
            if tile.card is not None or tile._dragging or getattr(tile, '_resizing', False)                     or getattr(tile, 'dragged', None) is not None:
                return True
            if tile.ID == 'apps' and tile.previews_visible():
                return True
        return False

    def keep_in_place(self) -> None:
        """The bar was seen away from where it belongs (once beside Settings, half off the screen):
        it is put back at once, and the move is written to the error log to find its cause."""
        if self.testing or self.tucked or self.sliding or self.moving or self._fit is not None \
                or not self.isVisible() or self.resting.isNull():
            return
        now = time.monotonic()
        if self.autohide() and now - getattr(self, '_placed_at', 0.0) > 2.0:   # where it belongs, again now and then
            self._placed_at = now
            self.place()
        if self.geometry() != self.resting:
            wrong = self.geometry()
            self.setGeometry(self.resting)
            try:
                from .config import folder
                with open(folder() / 'error.log', 'a', encoding='utf-8') as log:
                    log.write(f'--- {time.ctime()} dock on {self.device or "?"} was at {wrong.getRect()}, '
                              f'put back to {self.resting.getRect()}; screen {self.target_screen.name()} '
                              f'{self.target_screen.geometry().getRect()}; Settings open: '
                              f'{self.settings_window is not None}\n')
            except OSError:
                pass

    def watch_pointer(self) -> None:
        """Auto-hide: tucked away until the pointer reaches the screen's edge under it (never over a
        full-screen game), and away again half a second after the pointer leaves it."""
        if not self.screen_alive():
            return                                   # its display went: Docks moves it within moments
        if not self.autohide():
            self.keep_in_place()
            if self.tucked:
                self.tucked = False
                self.slide(True)
            return
        pos = QCursor.pos()
        g = self.target_screen.geometry()
        self.keep_in_place()
        if self.tucked:
            inside = g.left() <= pos.x() <= g.right()
            room = self.target_screen.availableGeometry()
            if self.edge == 'top':
                at_edge = pos.y() <= g.top() or (room.top() > g.top() and abs(pos.y() - room.top()) <= 1)
            else:                                    # the screen's edge, or the Windows taskbar's top
                at_edge = pos.y() >= g.bottom() - 1 or (room.bottom() < g.bottom() and abs(pos.y() - room.bottom()) <= 1)
            # a held button keeps it away (a window dragged to the edge), unless a file is being dragged
            # down to the dock, to be dropped on an app
            if inside and at_edge and (not win.left_button_down() or win.dragging_files()) \
                    and not win.full_screen_in_front(os.getpid(), self.device or None):
                self.place()                     # where it rests now (while still tucked)
                self.tucked = False
                self.away_since = None
                self.slide(True)
                self._poke_ai()
            return
        now = time.monotonic()
        if self.busy() or not self.isVisible():
            self.away_since = None
            return
        rise = round(self.tile_height * 1.3)
        zone = self.geometry().adjusted(-16, -rise if self.edge != 'top' else -16, 16, rise if self.edge == 'top' else 16)
        if self.edge == 'top':
            zone.setTop(g.top())
        else:
            zone.setBottom(g.bottom())
        if zone.contains(pos) or self.tray_open():
            if self.away_since is not None and self.away_since <= now:
                self.away_since = None
            return
        if self.away_since is None:
            self.away_since = now + 0.5
        elif now >= self.away_since:
            self.away_since = None
            self.tucked = True
            self.slide(False, lambda: self.tucked and self.hide())

    # ------------------------------------------------------------ displays

    def screen_changed(self, *_) -> None:
        self.place_soon.start()

    def screen_alive(self) -> bool:
        """The display it was given still exists (a monitor turned off is taken away by Qt)."""
        try:
            self.target_screen.geometry()
            return True
        except (RuntimeError, AttributeError):
            return False

    def move_to(self, screen) -> None:
        """Go to another display: the main dock follows Windows' main display, so turning the main
        monitor off brings the full dock to the one left, and back once the monitor returns."""
        if screen is self.target_screen and self.screen_alive():
            return
        if self._screen_connected and self.screen_alive():
            try:
                self.target_screen.geometryChanged.disconnect(self.screen_changed)
            except (RuntimeError, TypeError):
                pass
        self._screen_connected = False
        if self.appbar is not None:
            self.appbar.remove()
        self.target_screen = screen
        self._device = ''
        self.band = QRect()
        self.resting = QRect()
        if self.testing:
            return
        screen.geometryChanged.connect(self.screen_changed)
        self._screen_connected = True
        self.full_screen = False
        self.full_watch.stop()
        self.tucked = False
        self.away_since = time.monotonic() + 1.5 if self.autohide() else None
        if not self.isVisible():
            self.show()
        self.place()
        self.setGeometry(self.resting if not self.resting.isNull() else self.geometry())
        self._device = ''
        for tile in self.tiles:
            if tile.ID == 'apps':
                tile.refresh()

    @property
    def device(self) -> str:
        """Windows' name for this dock's display (Qt names it by the monitor's model instead): the
        display its own window is on."""
        if not getattr(self, '_device', ''):
            self._device = win.monitor_name(int(self.winId())) if not self.testing else ''
        return self._device

    def my_display(self, hwnd) -> bool:
        return win.monitor_name(hwnd) == self.device

    def shows_window(self, hwnd) -> bool:
        """Windows' 'Show my taskbar apps on': every dock, the main one and the window's own, or only
        the window's own display."""
        if not self.multi:
            return True
        mode = win.taskbar_settings()[1]
        if self.main:
            return mode != 2 or self.my_display(hwnd)
        return mode == 0 or self.my_display(hwnd)

    def shows_pinned(self) -> bool:
        return self.main or win.taskbar_settings()[1] == 0

    def start_guard(self) -> None:
        """A small watcher process that gives the Windows taskbar and the screen space back if
        Dockline is ended without quitting (Task Manager, a crash)."""
        if self.testing:
            return
        import subprocess
        own = [sys.executable] if getattr(sys, 'frozen', False) else [
            sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'main.py')]
        try:
            subprocess.Popen(own + ['--guard', str(os.getpid()), str(int(self.winId()))],
                             creationflags=0x08000000 | 0x00000200, close_fds=True)      # no window, own group
        except OSError:
            pass

    def nativeEvent(self, kind, message):
        if bytes(kind) == b'windows_generic_MSG':
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == win.APPBAR_MESSAGE:
                if msg.wParam == win.ABN_POSCHANGED:
                    self.place_soon.start()
                elif msg.wParam == win.ABN_FULLSCREENAPP:
                    QTimer.singleShot(150, self.check_full_screen)     # once the window has settled
            elif msg.message == win.SHELL_HOOK_MESSAGE:
                self.shell_notice(int(msg.wParam), int(msg.lParam or 0))
            elif msg.message == WM_QUERYENDSESSION:
                self.stop()
            elif msg.message == WM_DISPLAYCHANGE:
                self.place_soon.start()
        return False, 0

    def moveEvent(self, event):
        super().moveEvent(event)
        self.keep_magnifier()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.keep_magnifier()

    def keep_magnifier(self) -> None:
        """The magnified apps' layer stays over the strip as the dock slides or grows."""
        for tile in getattr(self, 'tiles', []):
            layer = getattr(tile, '_magnifier', None)
            if layer is not None and layer.isVisible():
                QTimer.singleShot(0, layer.place)        # once the row has been laid out at the new size

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)     # what was drawn before goes:
        painter.fillRect(self.rect(), Qt.transparent)                  # glass drawn over it showed it through
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing)
        look = self.look
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if look.glass:
            L.paint_clear_glass(painter, rect, 14, self.glass_fill(), look.dark)
            return
        painter.setPen(QPen(look.c(look.edge), 1))
        painter.setBrush(look.c(look.bar))
        painter.drawRoundedRect(rect, 14, 14)

    # ------------------------------------------------------------ moving the dock

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_from = event.globalPosition().toPoint()
        elif event.button() == Qt.RightButton:
            self.context_menu(None, event.globalPosition().toPoint(), event.position().toPoint())

    def mouseMoveEvent(self, event):
        if self._drag_from is None or not event.buttons() & Qt.LeftButton:
            return
        where = event.globalPosition().toPoint()
        if not self.moving and abs(where.y() - self._drag_from.y()) > 12:
            self.moving = True
            if self.appbar is not None:
                self.appbar.remove()
        if self.moving:
            self.move(self.x(), where.y() - self.height() // 2)

    def mouseReleaseEvent(self, event):
        if self.moving:
            screen_obj = self.target_screen if self.screen_alive() else QGuiApplication.primaryScreen()
            if screen_obj is not None:
                screen = screen_obj.geometry()
                top = event.globalPosition().y() < screen.center().y()
                self.cfg['edge'] = 'top' if top else 'bottom'
                self.cfg.save()
            self.moving = False
            self.place()
        self._drag_from = None

    def set_edge(self, edge: str) -> None:
        self.cfg['edge'] = edge
        self.cfg.save()
        self.place()

    # ------------------------------------------------------------ arranging widgets

    def begin_reorder(self, tile, pressed: QPoint) -> None:
        """A widget is picked up: it follows the pointer and the others make room, as the original's
        dock does."""
        if not self.main:
            return
        tile.raise_()
        self.drag_offset = pressed.x() - tile.mapToGlobal(QPoint(0, 0)).x()
        self.rest = {t: t.geometry() for t in self.tiles}

    def reorder_to(self, tile, where: QPoint) -> None:
        if not self.main:
            return
        x = self.mapFromGlobal(where).x()
        others = [t for t in self.tiles if t is not tile]
        index = sum(1 for t in others if self.rest.get(t, t.geometry()).center().x() < x)
        if self.tiles.index(tile) != index:
            seen = {t: t.pos() for t in others}
            self.tiles.remove(tile)
            self.tiles.insert(index, tile)
            self.row.removeWidget(tile)
            self.row.insertWidget(index, tile, 1 if tile.STRETCH else 0)
            self.relayout()
            self.rest = {t: t.geometry() for t in self.tiles}
            for other in others:
                if other.pos() != seen[other]:
                    self.glide(other, seen[other], other.pos())
        left = max(0, min(self.width() - tile.width(), x - (self.drag_offset - self.mapToGlobal(QPoint(0, 0)).x()
                                                             + self.mapToGlobal(QPoint(0, 0)).x())))
        tile.move(round(left), tile.y())

    def relayout(self) -> None:
        """Put every widget back where the row says, now. (activate() alone does nothing unless
        the row knows it changed, and a widget moved by hand does not tell it: dropped widgets were
        left where they were let go, on top of others.)"""
        self.row.invalidate()
        self.row.activate()

    def glide(self, widget, start: QPoint, end: QPoint) -> None:
        """Move a widget from where it shows to its new place, on the curve."""
        old = getattr(widget, '_glide', None)
        if old is not None:
            try:
                old.stop()
            except RuntimeError:
                pass
        widget.move(start)

        def step(progress):
            try:
                widget.move(round(start.x() + (end.x() - start.x()) * progress), end.y())
            except RuntimeError:
                pass

        def done():
            widget._glide = None
            widget.move(end)

        widget._glide = motion.run(widget, motion.SLIDE_MS, step, done)

    def end_reorder(self, tile) -> None:
        shown = tile.pos()
        self.relayout()
        self.glide(tile, shown, tile.pos())
        self.rest = {}
        if self.main:
            self.cfg['widgets'] = [t.ID for t in self.tiles]
            self.cfg.save()

    def add_widget(self, key: str) -> None:
        if key in REGISTRY and key not in self.cfg['widgets']:
            self.cfg['widgets'] = self.cfg['widgets'] + [key]
            self.cfg.save()
            self.rebuild()

    def remove_widget(self, key: str) -> None:
        self.cfg['widgets'] = [k for k in self.cfg['widgets'] if k != key]
        self.cfg.save()
        self.rebuild()

    def set_editing(self, on: bool) -> None:
        self.editing = on
        for tile in self.tiles:
            tile.update()

    # ------------------------------------------------------------ menus and windows

    def context_menu(self, tile, where: QPoint, local: QPoint) -> None:
        glass = self.look.glass and not self.testing
        menu = GlassMenu(self, glass)                    # the dock's glass, over the screen behind blurred
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        menu.setStyleSheet(menu_css(self.look, self.panel_fill() if glass else None))
        if glass:
            win.round_corners(int(menu.winId()), False)  # its own corners, round its frosted pane
        if tile is not None and getattr(tile, 'owns_menu', None) and tile.owns_menu(local):
            tile.menu(menu, local)                       # an app's own menu, nothing of the dock's
            menu.exec(where)
            menu.deleteLater()                           # one menu a right-click: not kept on the dock
            return
        if tile is not None:
            tile.menu(menu, local)
            if tile.resizable() and tile.ID in (self.cfg.get('widget_widths') or {}):
                menu.addAction('Reset width', tile.reset_width)
            menu.addAction(f'Remove {tile.NAME}', lambda: self.remove_widget(tile.ID))
            menu.addSeparator()
        menu.addAction('Settings…', self.open_settings)
        menu.addSeparator()
        if self.edge == 'top':
            menu.addAction('Move to bottom', lambda: self.set_edge('bottom'))
        else:
            menu.addAction('Move to top', lambda: self.set_edge('top'))
        menu.addAction('Show Windows taskbar' if self.cfg['hide_taskbar'] else 'Hide Windows taskbar',
                       lambda: self.set_hide_taskbar(not self.cfg['hide_taskbar']))
        menu.addSeparator()
        menu.addAction('Quit Dockline', QApplication.instance().quit)
        menu.exec(where)
        menu.deleteLater()

    def set_hide_taskbar(self, on: bool) -> None:
        self.cfg['hide_taskbar'] = on
        self.cfg.save()
        if self.testing:
            return
        if on:
            self.taskbar.hide()
        else:
            self.taskbar.restore()
        self.place_soon.start()
        if self.settings_window is not None:
            self.settings_window.sync()

    def open_picker(self) -> None:
        self.open_settings('widgets')

    def picker_closed(self) -> None:
        self.picker = None
        self.set_editing(False)

    def open_settings(self, page: str = '') -> None:
        from .settings_window import SettingsWindow
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
        if page:
            self.settings_window.open_page(page)
        if self.tucked:                              # Settings open: the dock comes out and stays out
            self.place()
            self.tucked = False
            self.away_since = None
            self.slide(True)
        self.settings_window.sync()
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()
        win.activate(int(self.settings_window.winId()))

    def settings_closed(self) -> None:
        self.settings_window = None

    def changed(self, what: str) -> None:
        """A setting changed in the Settings window."""
        self.cfg.save()
        if self.main and what not in ('taskbar', 'startup', 'weather'):
            for other in self.others:
                other.changed_here(what)
        self.changed_here(what)

    def settings_imported(self) -> None:
        """Settings were taken from an export: everything they cover, applied now."""
        for what in ('look', 'size', 'edge', 'opacity', 'clock', 'ai', 'weather', 'autohide', 'taskbar', 'startup'):
            self.changed(what)
        self.rebuild()
        self.place()
        for tile in self.tiles:
            if tile.ID == 'apps':
                tile.order = list(self.cfg['app_order'])
                tile.refresh()

    def changed_here(self, what: str) -> None:
        if what == 'autohide':
            self.tucked = False
            self.away_since = time.monotonic() + 1.0 if self.autohide() else None
            self.place()
            if not self.isVisible():
                self.slide(True)
            return
        if what in ('look', 'size'):
            self.apply()
        elif what == 'edge':
            self.place()
        elif what == 'opacity':
            self.update()
            for tile in self.tiles:
                tile.update()
        elif what in ('clock', 'ai'):
            if what == 'ai' and self.main:
                self.ai.configure()
            for tile in self.tiles:
                tile.update()
        elif what == 'weather':
            self.weather.latest = None
            for tile in self.tiles:
                if tile.ID == 'weather':
                    tile.fetch()
                    tile.update()
        elif what == 'taskbar' and self.main:
            self.set_hide_taskbar(self.cfg['hide_taskbar'])
        elif what == 'startup' and self.main:
            if not self.testing:
                set_startup(self.cfg['startup'])
