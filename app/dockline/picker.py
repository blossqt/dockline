"""The widget picker: browse by group or search, click a widget to add or remove it."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import look as L
from . import suitetheme as theme
from .widgets import GROUPS, WIDGETS

ALL = 'All widgets'


class _Choice(QFrame):
    """One widget in the picker: its name and what it does, pink while it is on the dock."""

    def __init__(self, widget, on: bool, toggled, preview: QPixmap | None = None):
        super().__init__()
        self.setObjectName('choice')
        self.setCursor(Qt.PointingHandCursor)
        self.key = widget.ID
        self.toggled = toggled
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        if preview is not None:
            picture = QLabel()
            picture.setPixmap(preview)
            picture.setObjectName('preview')
            layout.addWidget(picture, 0, Qt.AlignLeft)
            layout.addSpacing(8)
        name = QLabel(widget.NAME)
        name.setObjectName('name')
        about = QLabel(widget.ABOUT)
        about.setWordWrap(True)
        layout.addWidget(name)
        layout.addWidget(about)
        layout.addStretch(1)
        self.set_on(on)

    def set_on(self, on: bool) -> None:
        self.on = on
        self.setProperty('on', 'true' if on else 'false')
        for part in [self] + self.findChildren(QLabel):
            part.style().unpolish(part)
            part.style().polish(part)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.set_on(not self.on)
            self.toggled(self.key, self.on)

    BADGE = 22

    def paintEvent(self, event):
        """On the dock: the card as any other, its outline drawn in the chosen colour and a round
        check in its corner (a whole card in black among pale ones was jarring)."""
        super().paintEvent(event)
        if not self.on:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        fill = QColor(self.palette().color(self.foregroundRole()))
        badge = QRectF(self.width() - self.BADGE - 12, 12, self.BADGE, self.BADGE)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(badge)
        mark = QColor('#111114') if fill.lightness() > 128 else QColor('#fafafa')
        from PySide6.QtGui import QPen
        from PySide6.QtCore import QPointF
        painter.setPen(QPen(mark, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y, side = badge.x(), badge.y(), badge.width()
        painter.drawPolyline([QPointF(x + side * 0.28, y + side * 0.52), QPointF(x + side * 0.44, y + side * 0.67),
                              QPointF(x + side * 0.73, y + side * 0.36)])


class Picker(QWidget):
    def __init__(self, dock, embedded: bool = False):
        """A window of its own, or (embedded) the Widgets page of Settings."""
        super().__init__() if embedded else super().__init__(None, Qt.Window)
        self.dock = dock
        self.embedded = embedded
        self.samples: dict = {}                  # each widget's preview, drawn once a time it opens
        self.group = ALL
        if embedded:
            self.build_page()
            return
        self.setWindowTitle('Widgets')
        self.setWindowIcon(L.app_icon())
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setObjectName('window')
        self.setStyleSheet(L.settings_css(dock.look, dock.cfg.get('glass_opacity', 18)))
        self.resize(900, 640)

        side = QVBoxLayout()
        side.setContentsMargins(16, 16, 8, 16)
        side.setSpacing(16)
        title = QLabel('Widgets')
        title.setObjectName('title')
        side.addWidget(title)
        self.groups = QListWidget()
        self.groups.setFixedWidth(190)
        self.groups.addItems([ALL] + GROUPS)
        self.groups.setCurrentRow(0)
        self.groups.currentRowChanged.connect(lambda row: self.choose_group(([ALL] + GROUPS)[max(0, row)]))
        side.addWidget(self.groups, 1)

        main = QVBoxLayout()
        main.setContentsMargins(8, 16, 16, 16)
        main.setSpacing(16)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search widgets')
        self.search.textChanged.connect(lambda _: self.refresh())
        main.addWidget(self.search)
        note = QLabel('Click a widget to add or remove it. Drag widgets along the dock to reorder them, or '
                      'drag a widget\'s right handle to resize it (double-click the handle to reset).')
        note.setObjectName('sub')
        note.setWordWrap(True)
        main.addWidget(note)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main.addWidget(self.area, 1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(side)
        layout.addLayout(main, 1)
        self.refresh()

    def build_page(self) -> None:
        """Settings' Widgets page: groups as a row of buttons above the search (Settings has its own
        sidebar)."""
        self.setObjectName('root')
        main = QVBoxLayout(self)
        main.setContentsMargins(24, 24, 24, 16)
        main.setSpacing(16)
        title = QLabel('Widgets')
        title.setObjectName('title')
        main.addWidget(title)
        groups_box = QGridLayout()
        groups_box.setContentsMargins(0, 0, 0, 0)
        groups_box.setHorizontalSpacing(8)
        groups_box.setVerticalSpacing(8)
        buttons = QButtonGroup(self)
        names = [ALL] + GROUPS
        per_row = 4 if len(names) > 7 else len(names)
        for index, name in enumerate(names):
            button = QPushButton(name.replace('&', '&&'))         # '&' would be taken as a shortcut mark
            button.setCheckable(True)
            button.setChecked(name == ALL)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, n=name: self.choose_group(n))
            buttons.addButton(button)
            groups_box.addWidget(button, index // per_row, index % per_row)
        groups_box.setColumnStretch(per_row, 1)
        self.group_buttons = buttons
        main.addLayout(groups_box)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search widgets')
        self.search.textChanged.connect(lambda _: self.refresh())
        main.addWidget(self.search)
        note = QLabel('Click a widget to add or remove it. Drag widgets along the dock to reorder them, or '
                      'drag a widget\'s right handle to resize it (double-click the handle to reset).')
        note.setObjectName('sub')
        note.setWordWrap(True)
        main.addWidget(note)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main.addWidget(self.area, 1)
        self.refresh()

    def choose_group(self, name: str) -> None:
        self.group = name
        if hasattr(self, 'group_buttons'):
            for button in self.group_buttons.buttons():
                button.setChecked(button.text().replace('&&', '&') == name)
        self.refresh()

    def refresh(self) -> None:
        group = self.group
        words = self.search.text().strip().lower()
        chosen = set(self.dock.cfg['widgets'])
        shown = [w for w in WIDGETS
                 if (group == ALL or w.GROUP == group)
                 and (not words or words in w.NAME.lower() or words in w.ABOUT.lower())]
        host = QWidget()
        host.setObjectName('root')
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setSpacing(8)
        for index, widget in enumerate(shown):
            grid.addWidget(_Choice(widget, widget.ID in chosen, self.toggle, self.preview(widget)),
                           index // 2, index % 2)
        if not shown:
            empty = QLabel('No widgets match this search.')
            empty.setObjectName('sub')
            grid.addWidget(empty, 0, 0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(grid.rowCount(), 1)
        self.area.setWidget(host)

    def preview(self, widget) -> QPixmap | None:
        """The widget as it looks on the dock now: a sample drawn once, never shown on screen, on a
        strip of the dock's own colour."""
        if widget.ID in self.samples:
            return self.samples[widget.ID]
        picture = None
        try:
            tile = widget(self.dock)
            tile._preview = True
            try:
                tile.tick()
            except Exception:
                pass
            width = tile.sizeHint().width() if widget.ID == 'apps' else tile.width()
            tile.resize(max(1, min(width, 340)), self.dock.tile_height)
            # drawn onto a clear picture: grab() would fill the tile's clear background with white
            ratio = self.devicePixelRatioF() or 1.0
            shot = QPixmap(round(tile.width() * ratio), round(tile.height() * ratio))
            shot.setDevicePixelRatio(ratio)
            shot.fill(Qt.transparent)
            tile.render(shot, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
            tile.retire()
            tile.deleteLater()
            pad = 8
            size_w, size_h = shot.width() / ratio + 2 * pad, self.dock.bar_height
            picture = QPixmap(round(size_w * ratio), round(size_h * ratio))
            picture.setDevicePixelRatio(ratio)
            picture.fill(Qt.transparent)
            painter = QPainter(picture)
            painter.setRenderHint(QPainter.Antialiasing)
            look = self.dock.look
            ground = (QColor(32, 32, 38) if look.dark else QColor(232, 232, 236)) if look.glass else look.c(look.bar)
            ground.setAlpha(255)
            painter.setPen(Qt.NoPen)
            painter.setBrush(ground)
            painter.drawRoundedRect(QRectF(0, 0, size_w, size_h), 12, 12)
            painter.drawPixmap(pad, round((size_h - shot.height() / ratio) / 2), shot)
            painter.end()
        except Exception:
            picture = None
        self.samples[widget.ID] = picture
        return picture

    def toggle(self, key: str, on: bool) -> None:
        if on:
            self.dock.add_widget(key)
        else:
            self.dock.remove_widget(key)

    def closeEvent(self, event):
        if not self.embedded:
            self.dock.picker_closed()
        super().closeEvent(event)
