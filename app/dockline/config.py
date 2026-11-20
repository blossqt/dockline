"""Settings, kept as JSON in %APPDATA%\\Dockline\\settings.json (DOCKLINE_SETTINGS overrides it)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULT_WIDGETS = ['start', 'search', 'taskview', 'apps', 'media', 'volume', 'network', 'cpu', 'ram', 'ai_limits', 'weather', 'tray', 'clock']
# widgets added to an existing dock once, when a version brings them (the person can remove them)
NEW_WIDGETS = {'tray': 'clock', 'taskview': 'apps', 'language': 'clock', 'ai_limits': 'weather'}      # widget: put before this one
ONLY_WITH_TWO_KEYBOARDS = {'language'}      # offered only where Windows has more than one keyboard language

DEFAULTS = {
    'look': 'glass',            # black, white, glass, midnight
    'size': 'M',                # S, M, L
    'edge': 'bottom',           # bottom, top
    'clock24': False,
    'fahrenheit': False,
    'hide_taskbar': False,
    'autohide': True,           # tucked away until the pointer reaches the screen's edge
    'alerts': True,             # a hidden dock comes out briefly for attention, timers and notifications
    'alert_notices': True,      # ...and Windows notifications among them
    'startup': False,
    'widgets': DEFAULT_WIDGETS,
    'pinned': [],
    'pins_imported': False,     # the Windows taskbar's pins were brought over once
    'app_order': [],            # the apps' order as arranged by dragging
    'offered': [],              # new widgets already added once
    'glass_opacity': 18,        # no longer used: the Glass style's tint is Hanami's (Settings > Appearance > Glass)
    'ai_show_used': False,      # False: percent left; True: percent used
    'ai_tile_style': 'rings',   # rings, gauges, or bars on the dock widget
    'ai_refresh': 'live',       # live (watch local sessions + 45s sync), 30s, 1m, or 5m
    'ai_warn_low': True,        # show the dock briefly when an active AI window drops to 15% left
    'ai_enabled': ['claude', 'chatgpt', 'gemini'],  # which AI services appear in AI Limits
    'ai_pair': ['claude', 'chatgpt'],   # preferred two plans on the combined AI Limits tile
    'widget_widths': {},        # custom widget widths in units of tile height, set by dragging handles
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


# ---------------------------------------------------------------- export and import

EXPORT_KIND = 'dockline-settings'
NOT_EXPORTED = {'taskbar_saved'}            # this PC's state while the dock runs, not a setting


def export_settings(cfg: 'Config', path: Path) -> None:
    """Write every setting to a file, with the pinned shortcuts Dockline keeps (Pins), so another PC
    or a new install takes the same dock."""
    import base64
    data = {key: value for key, value in dict(cfg).items() if key not in NOT_EXPORTED}
    pins = folder() / 'Pins'
    shortcuts = {}
    for pinned in cfg['pinned']:
        file = Path(pinned)
        try:
            if file.parent.resolve() == pins.resolve() and file.exists():
                shortcuts[file.name] = base64.b64encode(file.read_bytes()).decode('ascii')
        except OSError:
            continue
    from . import __version__
    Path(path).write_text(json.dumps({'kind': EXPORT_KIND, 'version': __version__, 'settings': data,
                                      'shortcuts': shortcuts}, indent=2), 'utf-8')


class ImportError_(ValueError):
    pass


def import_settings(cfg: 'Config', path: Path) -> None:
    """Take every setting from an exported file (shortcuts put back into Pins), and save."""
    import base64
    try:
        payload = json.loads(Path(path).read_text('utf-8'))
    except (OSError, ValueError) as error:
        raise ImportError_(f'The file could not be read ({error}).') from None
    if not isinstance(payload, dict) or payload.get('kind') != EXPORT_KIND or not isinstance(payload.get('settings'), dict):
        raise ImportError_('The file is not a Dockline settings export.')
    pins = folder() / 'Pins'
    placed = {}
    for name, encoded in (payload.get('shortcuts') or {}).items():
        name = Path(name).name                          # a file name only, never a path
        if not name.lower().endswith('.lnk'):
            continue
        try:
            pins.mkdir(parents=True, exist_ok=True)
            (pins / name).write_bytes(base64.b64decode(encoded))
            placed[name.lower()] = str(pins / name)
        except (OSError, ValueError):
            continue
    settings = payload['settings']
    for key, default in DEFAULTS.items():
        if key in NOT_EXPORTED or key not in settings:
            continue
        value = settings[key]
        if default is not None and value is not None and not isinstance(value, type(default)) \
                and not (isinstance(default, (int, float)) and isinstance(value, (int, float))):
            continue                                    # a value of the wrong kind is left as it is here
        cfg[key] = value
    cfg['pinned'] = [placed.get(Path(p).name.lower(), p) for p in cfg['pinned']]
    cfg['pins_imported'] = True                         # not the Windows taskbar's again on top
    cfg.save()
