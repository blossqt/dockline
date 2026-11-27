"""Settings: appearance, dock size and edge, clock, weather location, the taskbar and startup.

Laid out as Hanami's and the other suite apps' settings are (suitesettings.SettingsFrame): the
suite's glass with its faint blossom, the pages down the left with icons and a pink bar sliding to
the one chosen, Geist Serif titles, rounded sections, switches and segmented choices."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import __version__
from . import look as L
from . import suitesettings
from . import suitetheme as theme
from . import weather
from .suitesettings import SMALL, label, note, section, switch

BACKDROP = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backdrop.jpg')   # Hanami's blossom, faint


class _Found(QObject):
    done = Signal(object, str)


def _choice(options: list[tuple[str, str]], current, changed) -> QWidget:
    """A choice of a few, as Hanami's: a soft track with a frosted pill sliding to the one chosen."""
    group = QButtonGroup()
    buttons = []
    for value, words in options:
        button = QPushButton(words)
        button.setCheckable(True)
        button.setChecked(value == current)
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(lambda _=False, v=value: changed(v))
        group.addButton(button)
        buttons.append(button)
    control = suitesettings.Segmented(buttons)
    group.setParent(control)
    control.buttons_group = group
    return control


class _Rows(QGridLayout):
    """Settings with a name before each, the names in a column on the left, each centred on its
    choice's height (a form layout put them at the choices' tops)."""

    def __init__(self):
        super().__init__()
        self.setContentsMargins(0, 0, 0, 0)
        self.setHorizontalSpacing(16)
        self.setVerticalSpacing(suitesettings.ITEMS)
        self.setColumnStretch(1, 1)

    def addRow(self, name, field) -> None:
        row = self.rowCount()
        self.addWidget(name, row, 0, Qt.AlignLeft)        # the row's height: its letters centred (theme.Label)
        self.addWidget(field, row, 1, Qt.AlignVCenter)


def _button(words: str, slot) -> QPushButton:
    button = QPushButton(words)
    button.setCursor(Qt.PointingHandCursor)
    button.clicked.connect(slot)
    return button


class _Reveal(QObject):
    """A page's sections rising into place as scrolling brings them into sight - each once, as
    the page's first ones do when it comes up."""

    def __init__(self, area: QScrollArea):
        super().__init__(area)
        self.area = area
        self.seen: set[int] = set()
        area.verticalScrollBar().valueChanged.connect(self.look)
        area.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Show, QEvent.Resize):
            self.mark_in_sight()
        return False

    def mark_in_sight(self) -> None:
        """What shows without scrolling is the page's own coming in, not this."""
        for frame in self.sections():
            if self.top_of(frame) < self.area.viewport().height():
                self.seen.add(id(frame))

    def sections(self) -> list:
        page = self.area.widget()
        return [] if page is None else [f for f in page.findChildren(QFrame, 'section') if f.isVisibleTo(page)]

    def top_of(self, frame) -> int:
        return frame.mapTo(self.area.widget(), frame.rect().topLeft()).y() - self.area.verticalScrollBar().value()

    def look(self, _value=None) -> None:
        coming = []
        bottom = self.area.viewport().height()
        for frame in self.sections():
            if id(frame) in self.seen:
                continue
            if self.top_of(frame) < bottom - 8:
                self.seen.add(id(frame))
                coming.append(frame)
        if coming:
            theme.stagger_in(coming, by=14, ms=theme.GENTLE + 60)


class SettingsWindow(QWidget):
    PAGES = [('gear', 'General'), ('palette', 'Appearance'), ('clock', 'Clock and weather'), ('grid', 'Widgets'),
             ('info', 'About')]
    WIDGETS_PAGE = 3

    def __init__(self, dock):
        super().__init__(None, Qt.Window)
        self.dock = dock
        self.cfg = dock.cfg
        self.setWindowTitle('Dockline Settings')
        self.setWindowIcon(L.app_icon())
        self.setAttribute(Qt.WA_DeleteOnClose)
        if not dock.testing:
            theme.frost(self, backdrop=BACKDROP)      # the suite's glass, as Hanami's settings
            theme.closes_on_escape(self)
        self.resize(940, 680)
        self.page_index = 0
        self.picker = None
        self.found = _Found()
        self.found.done.connect(self.place_found)
        self.build()

    def build(self) -> None:
        frame = suitesettings.SettingsFrame(self, L.app_icon().pixmap(28, 28))
        self.frame = frame
        self.nav, self.pages = frame.nav, frame.pages
        self.reveals = []
        for (mark, title), make in zip(self.PAGES, (self.general, self.appearance, self.clock_page, None,
                                                    self.about)):
            if make is None:
                from .picker import Picker
                self.picker = Picker(self.dock, embedded=True)
                frame.add_widget(mark, title, self.picker)
                continue
            page, layout = suitesettings.page(title)
            make(layout)
            layout.addStretch(1)
            area = suitesettings.scrolled(page)
            self.reveals.append(_Reveal(area))
            frame.add_widget(mark, title, area)
        self.nav.chosen.connect(self.show_page)

    # ------------------------------------------------------------------ pages

    def general(self, layout: QVBoxLayout) -> None:
        box_frame, box = section('Starting up')
        self.switch(box, 'Start with Windows', 'startup', 'startup', 'Dockline starts when you sign in.')
        layout.addWidget(box_frame)

        box_frame, box = section('The Windows taskbar')
        self.hide_box = self.switch(box, 'Hide the Windows taskbar', 'hide_taskbar', 'taskbar',
                                    'The taskbar and its notification area are hidden while Dockline runs and '
                                    'return when it quits.')
        self.switch(box, 'Hide the dock automatically', 'autohide', 'autohide',
                    'The dock appears when the pointer reaches the edge of the screen, except over '
                    'full-screen apps.')
        self.switch(box, 'Show the dock for alerts', 'alerts', 'alerts',
                    'A hidden dock appears briefly when an app requests attention or a timer ends.')
        self.switch(box, 'Show the dock for notifications', 'alert_notices', 'alerts',
                    'Applies to apps with notification banners on in Windows Settings.')
        box.addWidget(note('On more than one display, Dockline follows the Windows taskbar settings '
                           '"Show my taskbar on all displays" and "Show my taskbar apps on".'))
        layout.addWidget(box_frame)

        box_frame, box = section('Pinned apps')
        pins_note = note('Adds the apps pinned to the Windows taskbar to the dock. Programs and shortcuts '
                         'dragged onto the dock are pinned too.')

        def import_now():
            added = self.dock.import_taskbar_pins()
            pins_note.setText('No new pinned apps found on the Windows taskbar.' if added == 0 else
                              f'{added} pinned {"app" if added == 1 else "apps"} added to the dock.')
        row = QHBoxLayout()
        row.addWidget(_button('Import taskbar pins', import_now))
        row.addStretch(1)
        box.addLayout(row)
        box.addWidget(pins_note)
        layout.addWidget(box_frame)

        box_frame, box = section('Backup')
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(_button('Export settings', self.export_settings))
        row.addWidget(_button('Import settings', self.import_settings))
        row.addStretch(1)
        box.addLayout(row)
        self.backup_note = note('Includes the widgets and their order, pinned apps, style, AI preferences, '
                                'weather location and notes.')
        box.addWidget(self.backup_note)
        layout.addWidget(box_frame)

    def appearance(self, layout: QVBoxLayout) -> None:
        cfg = self.cfg
        box_frame, box = section('The dock')
        rows = _Rows()
        rows.addRow(label('Style'), self.choice([(k, L.LOOKS[k].label) for k in L.STYLES], cfg['look'],
                                                lambda v: self.set('look', v, 'look')))
        rows.addRow(label('Size'), self.choice([('S', 'Small'), ('M', 'Medium'), ('L', 'Large')], cfg['size'],
                                               lambda v: self.set('size', v, 'size')))
        rows.addRow(label('Position'), self.choice([('bottom', 'Bottom'), ('top', 'Top')], cfg['edge'],
                                                   lambda v: self.set('edge', v, 'edge')))
        box.addLayout(rows)
        box.addWidget(note('Glass transparency, light or dark, and animations are set for the whole suite in '
                           'Hanami: Settings > Appearance.'))
        layout.addWidget(box_frame)

    def clock_page(self, layout: QVBoxLayout) -> None:
        cfg = self.cfg
        box_frame, box = section('Clock')
        rows = _Rows()
        rows.addRow(label('Clock'), self.choice([(False, '12-hour'), (True, '24-hour')], cfg['clock24'],
                                                lambda v: self.set('clock24', v, 'clock')))
        rows.addRow(label('Temperature'), self.choice([(False, 'Celsius'), (True, 'Fahrenheit')], cfg['fahrenheit'],
                                                      lambda v: self.set('fahrenheit', v, 'weather')))
        box.addLayout(rows)
        layout.addWidget(box_frame)

        box_frame, box = section('Weather location')
        row = QHBoxLayout()
        row.setSpacing(8)
        self.city = QLineEdit(cfg['city'])
        self.city.setPlaceholderText('City')
        self.city.returnPressed.connect(self.find_city)
        row.addWidget(self.city, 1)
        row.addWidget(_button('Set location', self.find_city))
        box.addLayout(row)
        self.city_note = note('Weather data from Open-Meteo.' if cfg['latitude'] is None else
                              f'Location: {cfg["city"]}')
        box.addWidget(self.city_note)
        layout.addWidget(box_frame)

    def about(self, layout: QVBoxLayout) -> None:
        box_frame, box = section('')
        row = QHBoxLayout()
        row.setSpacing(16)
        logo = theme.Label()
        logo.setPixmap(L.app_icon().pixmap(64, 64))
        row.addWidget(logo, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(16)
        words.addWidget(suitesettings.heading(f'Dockline {__version__}', 17))
        words.addWidget(label('Right-click the dock to add widgets, move it or quit. Drag the dock by an empty '
                              'spot to move it between the top and bottom of the screen.', SMALL, role='dim',
                              wrap=True))
        buttons = QHBoxLayout()
        buttons.addWidget(_button('Quit Dockline', QApplication.instance().quit))
        buttons.addStretch(1)
        words.addLayout(buttons)
        row.addLayout(words, 1)
        box.addLayout(row)
        layout.addWidget(box_frame)

    # ------------------------------------------------------------------ parts

    def choice(self, options, current, changed) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(_choice(options, current, changed))
        row.addStretch(1)
        return holder

    def switch(self, box: QVBoxLayout, words: str, key: str, what: str, about: str = '') -> QCheckBox:
        holder, check = switch(words, about, bool(self.cfg[key]))
        check.toggled.connect(lambda on: self.set(key, on, what))
        box.addWidget(holder)
        return check

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

    def set(self, key: str, value, what: str) -> None:
        if self.cfg[key] == value:
            return
        self.cfg[key] = value
        self.dock.changed(what)
        if key == 'look' and self.picker is not None:      # the widgets' previews, in the new style
            self.picker.samples.clear()
            if self.page_index == self.WIDGETS_PAGE:
                self.picker.refresh()

    def reopen(self) -> None:
        where, page = self.geometry(), self.PAGES[self.page_index][1]
        self.close()
        self.dock.open_settings(page.lower())
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
        """On Widgets, the dock shows its remove badges, as with the picker."""
        self.page_index = max(0, index)
        widgets = self.page_index == self.WIDGETS_PAGE
        self.dock.picker = self.picker if widgets else None
        self.dock.set_editing(widgets)
        if widgets:
            self.picker.refresh()

    def open_page(self, name: str) -> None:
        self.frame.open_page(name)              # 'general', 'widgets', or any page's title

    def closeEvent(self, event):
        self.dock.picker = None
        self.dock.set_editing(False)
        self.dock.settings_closed()
        super().closeEvent(event)
