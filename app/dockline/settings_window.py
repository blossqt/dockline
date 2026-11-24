"""Settings: appearance, dock size and edge, clock, weather location, the taskbar and startup."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from . import __version__
from . import look as L
from . import suitetheme as theme
from . import weather


class _Found(QObject):
    done = Signal(object, str)


def _choice(options: list[tuple[str, str]], current: str, changed) -> QWidget:
    """A row of buttons of which one is chosen."""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    group = QButtonGroup(host)
    group.setExclusive(True)
    for value, label in options:
        button = QPushButton(label)
        button.setCheckable(True)
        button.setChecked(value == current)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda _=False, v=value: changed(v))
        group.addButton(button)
        row.addWidget(button)
    row.addStretch(1)
    host.buttons = group
    return host


class SettingsWindow(QWidget):
    def __init__(self, dock):
        super().__init__(None, Qt.Window)
        self.dock = dock
        self.cfg = dock.cfg
        self.setWindowTitle('Dockline Settings')
        self.setWindowIcon(L.app_icon())
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.glass = dock.look.glass and not dock.testing
        if self.glass:                               # see-through to Windows' blur, as the dock's glass
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.restyle_later = QTimer(self)
        self.restyle_later.setSingleShot(True)
        self.restyle_later.setInterval(120)
        self.restyle_later.timeout.connect(self.restyle)
        self.resize(900, 720)
        self.page_index = 0
        self.picker = None
        self.found = _Found()
        self.found.done.connect(self.place_found)
        self.build()

    def build(self) -> None:
        self.setObjectName('window')
        self.restyle()
        if self.layout() is not None:
            QWidget().setLayout(self.layout())          # drop the old layout and its widgets
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # the sidebar: everything Dockline has, in one place (the dock's menu opens the same pages)
        side = QVBoxLayout()
        side.setContentsMargins(16, 24, 8, 16)
        side.setSpacing(16)
        name = QLabel('Dockline')
        name.setObjectName('title')
        side.addWidget(name)
        self.nav = QListWidget()
        self.nav.setFixedWidth(170)
        self.nav.addItems(['General', 'Widgets'])
        side.addWidget(self.nav, 1)
        outer.addLayout(side)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.viewport().installEventFilter(theme._wheels())     # the suite's smooth wheel, here only
        self.pages.addWidget(scroll)
        from .picker import Picker
        self.picker = Picker(self.dock, embedded=True)
        self.pages.addWidget(self.picker)
        self.nav.currentRowChanged.connect(self.show_page)
        self.nav.setCurrentRow(self.page_index)
        page = QWidget()
        page.setObjectName('root')
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        title = QLabel('General')
        title.setObjectName('title')
        layout.addWidget(title)

        cfg = self.cfg
        appearance = self.section(layout, 'Appearance')
        self.row(appearance, 'Style', _choice([(k, L.LOOKS[k].label) for k in L.STYLES], cfg['look'],
                                              lambda v: self.set('look', v, 'look', rebuild=True)))
        glass_note = QLabel('Glass transparency is set in Hanami: Settings > Appearance > Glass.')
        glass_note.setObjectName('sub')
        appearance.addWidget(glass_note)
        self.row(appearance, 'Dock size', _choice([('S', 'Small'), ('M', 'Medium'), ('L', 'Large')], cfg['size'],
                                                  lambda v: self.set('size', v, 'size')))
        self.row(appearance, 'Position', _choice([('bottom', 'Bottom'), ('top', 'Top')], cfg['edge'],
                                                 lambda v: self.set('edge', v, 'edge')))

        clock = self.section(layout, 'Clock and weather')
        self.row(clock, 'Clock', _choice([(False, '12-hour'), (True, '24-hour')], cfg['clock24'],
                                         lambda v: self.set('clock24', v, 'clock')))
        self.row(clock, 'Temperature', _choice([(False, 'Celsius'), (True, 'Fahrenheit')], cfg['fahrenheit'],
                                               lambda v: self.set('fahrenheit', v, 'weather')))
        city_row = QHBoxLayout()
        city_row.setSpacing(8)
        self.city = QLineEdit(cfg['city'])
        self.city.setPlaceholderText('City')
        self.city.returnPressed.connect(self.find_city)
        find = QPushButton('Set location')
        find.setFocusPolicy(Qt.NoFocus)
        find.clicked.connect(self.find_city)
        city_row.addWidget(self.city, 1)
        city_row.addWidget(find)
        self.row(clock, 'Weather location', city_row)
        self.city_note = QLabel('Weather data from Open-Meteo.' if cfg['latitude'] is None else
                                f'Location: {cfg["city"]}')
        self.city_note.setObjectName('sub')
        clock.addWidget(self.city_note)

        system = self.section(layout, 'Windows')
        self.hide_box = self.check(system, 'Hide the Windows taskbar', 'hide_taskbar', 'taskbar',
                                   'The taskbar and its notification area are hidden while Dockline runs and '
                                   'return when it quits.')
        self.check(system, 'Hide the dock automatically', 'autohide', 'autohide',
                   'The dock appears when the pointer reaches the edge of the screen, except over '
                   'full-screen apps.')
        self.check(system, 'Show the dock for alerts', 'alerts', 'alerts',
                   'A hidden dock appears briefly when an app requests attention or a timer ends.')
        self.check(system, 'Show the dock for notifications', 'alert_notices', 'alerts',
                   'Applies to apps with notification banners on in Windows Settings.')
        self.check(system, 'Start with Windows', 'startup', 'startup')
        pins_row = QHBoxLayout()
        import_pins = QPushButton('Import taskbar pins')
        import_pins.setFocusPolicy(Qt.NoFocus)
        import_pins.setCursor(Qt.PointingHandCursor)
        pins_row.addWidget(import_pins)
        pins_row.addStretch(1)
        system.addLayout(pins_row)
        pins_note = QLabel('Adds the apps pinned to the Windows taskbar to the dock. Programs and shortcuts '
                           'dragged onto the dock are pinned too.')
        pins_note.setObjectName('sub')
        pins_note.setWordWrap(True)
        system.addWidget(pins_note)

        def import_now():
            added = self.dock.import_taskbar_pins()
            pins_note.setText('No new pinned apps found on the Windows taskbar.' if added == 0 else
                              f'{added} pinned {"app" if added == 1 else "apps"} added to the dock.')

        import_pins.clicked.connect(import_now)
        displays = QLabel('On more than one display, Dockline follows the Windows taskbar settings '
                          '"Show my taskbar on all displays" and "Show my taskbar apps on".')
        displays.setObjectName('sub')
        displays.setWordWrap(True)
        system.addWidget(displays)

        backup = self.section(layout, 'Backup')
        backup_row = QHBoxLayout()
        backup_row.setSpacing(8)
        for words, slot in (('Export settings', self.export_settings), ('Import settings', self.import_settings)):
            button = QPushButton(words)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(slot)
            backup_row.addWidget(button)
        backup_row.addStretch(1)
        backup.addLayout(backup_row)
        self.backup_note = QLabel('Includes the widgets and their order, pinned apps, style, AI preferences, '
                                  'weather location and notes.')
        self.backup_note.setObjectName('sub')
        self.backup_note.setWordWrap(True)
        backup.addWidget(self.backup_note)

        about = self.section(layout, 'About')
        version = QLabel(f'Dockline {__version__}')
        about.addWidget(version)
        quit_row = QHBoxLayout()
        quit_button = QPushButton('Quit Dockline')
        quit_button.setFocusPolicy(Qt.NoFocus)
        quit_button.clicked.connect(QApplication.instance().quit)
        quit_row.addWidget(quit_button)
        quit_row.addStretch(1)
        about.addLayout(quit_row)
        note = QLabel('Right-click the dock to add widgets, move it or quit. Drag the dock by an empty spot to '
                      'move it between the top and bottom of the screen.')
        note.setObjectName('sub')
        note.setWordWrap(True)
        about.addWidget(note)
        layout.addStretch(1)

    def export_settings(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        from .config import export_settings
        path, _ = QFileDialog.getSaveFileName(self, 'Export settings', str(Path.home() / 'Documents' /
                                                                           'Dockline settings.json'),
                                              'Dockline settings (*.json)')
        if not path:
            return
        try:
            export_settings(self.cfg, Path(path))
            self.backup_note.setText(f'Settings exported to {path}.')
        except OSError as error:
            self.backup_note.setText(f'The settings could not be exported ({error.strerror or error}). '
                                     'Choose another folder and try again.')

    def import_settings(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        from .config import ImportError_, import_settings
        path, _ = QFileDialog.getOpenFileName(self, 'Import settings', str(Path.home() / 'Documents'),
                                              'Dockline settings (*.json)')
        if not path:
            return
        try:
            import_settings(self.cfg, Path(path))
        except ImportError_ as error:
            self.backup_note.setText(str(error))
            return
        self.dock.settings_imported()
        QTimer.singleShot(0, self.reopen)                # the window shows the imported settings

    def section(self, layout: QVBoxLayout, title: str) -> QVBoxLayout:
        heading = QLabel(title)
        heading.setObjectName('heading')
        layout.addWidget(heading)
        frame = QFrame()
        frame.setObjectName('section')
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(16, 16, 16, 16)
        inner.setSpacing(16)
        layout.addWidget(frame)
        return inner

    def row(self, section: QVBoxLayout, label: str, control) -> None:
        box = QVBoxLayout()
        box.setSpacing(8)
        name = QLabel(label)
        box.addWidget(name)
        if isinstance(control, QWidget):
            box.addWidget(control)
        else:
            box.addLayout(control)
        section.addLayout(box)

    def check(self, section: QVBoxLayout, label: str, key: str, what: str, line: str = '') -> QCheckBox:
        box = QCheckBox(label)
        box.setChecked(bool(self.cfg[key]))
        box.setCursor(Qt.PointingHandCursor)
        box.toggled.connect(lambda on: self.set(key, on, what))
        section.addWidget(box)
        if line:
            note = QLabel(line)
            note.setObjectName('sub')
            note.setWordWrap(True)
            section.addWidget(note)
        return box

    def restyle(self) -> None:
        """The dock's style, now (and its glass's tint)."""
        self.setStyleSheet(L.settings_css(self.dock.look, self.dock.glass_percent()))

    def showEvent(self, event):
        super().showEvent(event)
        if not self.dock.testing:
            from . import win
            win.framed_look(int(self.winId()), self.dock.look.dark, self.glass)

    def set(self, key: str, value, what: str, rebuild: bool = False) -> None:
        if self.cfg[key] == value:
            return
        self.cfg[key] = value
        self.dock.changed(what)
        if key == 'look':                            # a new style: the window is made again in it
            QTimer.singleShot(0, self.reopen)
        elif rebuild:
            self.build()

    def reopen(self) -> None:
        where, page = self.geometry(), ('general', 'widgets')[self.page_index]
        self.close()
        self.dock.open_settings(page)
        if self.dock.settings_window is not None:
            self.dock.settings_window.setGeometry(where)

    def sync(self) -> None:
        """Follow changes made from the dock's menu."""
        if hasattr(self, 'hide_box'):
            self.hide_box.blockSignals(True)
            self.hide_box.setChecked(bool(self.cfg['hide_taskbar']))
            self.hide_box.blockSignals(False)

    def find_city(self) -> None:
        name = self.city.text().strip()
        if not name:
            return
        self.city_note.setText('Finding the location.')

        def look_up():
            try:
                self.found.done.emit(weather.find_place(name), '')
            except Exception as error:
                self.found.done.emit(None, str(error))

        threading.Thread(target=look_up, daemon=True).start()

    def place_found(self, place, error: str) -> None:
        try:
            if place is None:
                self.city_note.setText('The location was not found. Check the network connection.' if error else
                                       'No city of that name was found.')
                return
            label = f'{place["name"]}, {place["country"]}' if place['country'] else place['name']
            self.cfg['city'] = place['name']
            self.cfg['latitude'] = place['latitude']
            self.cfg['longitude'] = place['longitude']
            self.city.setText(place['name'])
            self.city_note.setText(f'Location: {label}')
            self.dock.changed('weather')
        except RuntimeError:
            pass                                       # the window closed meanwhile

    def show_page(self, index: int) -> None:
        """General or Widgets. On Widgets, the dock shows its remove badges, as with the picker."""
        self.page_index = max(0, index)
        self.pages.setCurrentIndex(self.page_index)
        widgets = self.page_index == 1
        self.dock.picker = self.picker if widgets else None
        self.dock.set_editing(widgets)
        if widgets:
            self.picker.refresh()

    def open_page(self, name: str) -> None:
        self.nav.setCurrentRow({'general': 0, 'widgets': 1}.get(name, 0))

    def closeEvent(self, event):
        self.dock.picker = None
        self.dock.set_editing(False)
        self.dock.settings_closed()
        super().closeEvent(event)
