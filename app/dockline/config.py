"""Settings, kept as JSON in %APPDATA%\\Dockline\\settings.json (DOCKLINE_SETTINGS overrides it)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULT_WIDGETS = ['start', 'search', 'taskview', 'apps', 'media', 'volume', 'network', 'cpu', 'ram', 'weather', 'tray', 'clock']
# widgets added to an existing dock once, when a version brings them (the person can remove them)
NEW_WIDGETS = {'tray': 'clock', 'taskview': 'apps'}      # widget: put before this one

DEFAULTS = {
    'look': 'glass',            # black, white, glass, midnight
    'size': 'M',                # S, M, L
    'edge': 'bottom',           # bottom, top
    'clock24': False,
    'fahrenheit': False,
    'hide_taskbar': False,
    'autohide': True,           # tucked away until the pointer reaches the screen's edge
    'startup': False,
    'widgets': DEFAULT_WIDGETS,
    'pinned': [],
    'app_order': [],            # the apps' order as arranged by dragging
    'offered': [],              # new widgets already added once
    'glass_opacity': 18,        # the Glass style's tint, in percent: lower is clearer
    'notes': '',
    'city': '',
    'latitude': None,
    'longitude': None,
    'taskbar_saved': None,      # the taskbar's state before the dock hid it, until it is restored
}


def folder() -> Path:
    return Path(os.environ.get('APPDATA') or Path.home()) / 'Dockline'


class Config(dict):
    def __init__(self, path: Path | None):
        super().__init__(copy.deepcopy(DEFAULTS))
        self.path = path
        if path and path.exists():
            try:
                data = json.loads(path.read_text('utf-8'))
                self.update({key: value for key, value in data.items() if key in DEFAULTS})
            except (OSError, ValueError):
                pass

    @classmethod
    def load(cls) -> 'Config':
        return cls(Path(os.environ.get('DOCKLINE_SETTINGS') or folder() / 'settings.json'))

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            spare = self.path.with_suffix('.tmp')
            spare.write_text(json.dumps(dict(self), indent=2), 'utf-8')
            os.replace(spare, self.path)
        except OSError:
            pass
