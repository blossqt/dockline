"""Shortcuts (.lnk files) and the Windows taskbar's own pins.

A pinned shortcut is matched to the windows of the program it starts, as the taskbar does, so an
open program shows on its pin rather than beside it. The shortcut's target and arguments are read
from the file itself (MS-SHLLINK), without COM.
"""

from __future__ import annotations

import os
import re
import struct
import winreg
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HAS_ID_LIST, HAS_LINK_INFO, HAS_NAME, HAS_RELATIVE_PATH = 0x1, 0x2, 0x4, 0x8
HAS_WORKING_DIR, HAS_ARGUMENTS, HAS_ICON_LOCATION, IS_UNICODE = 0x10, 0x20, 0x40, 0x80
ENVIRONMENT_BLOCK = 0xA0000001

PROGRAMS = ('.exe', '.lnk', '.appref-ms', '.url', '.bat', '.cmd', '.msc')


@dataclass(frozen=True)
class Shortcut:
    path: str
    target: str = ''          # the program or file it starts, '' when it names none (a Store app)
    arguments: str = ''
    working_dir: str = ''
    icon: str = ''            # the file its icon is taken from (environment variables expanded), if it names one
    icon_index: int = 0


def _cstring(data: bytes, at: int, wide: bool) -> str:
    if wide:
        end = at
        while end + 1 < len(data) and data[end:end + 2] != b'\0\0':
            end += 2
        return data[at:end].decode('utf-16-le', 'replace')
    end = data.find(b'\0', at)
    return data[at:end if end >= 0 else len(data)].decode('mbcs', 'replace')


def parse(data: bytes, path: str = '') -> Shortcut:
    """The target, arguments and working folder of a shortcut file's bytes."""
    if len(data) < 0x4C or struct.unpack_from('<I', data, 0)[0] != 0x4C:
        raise ValueError('not a shortcut')
    flags = struct.unpack_from('<I', data, 20)[0]
    icon_index = struct.unpack_from('<i', data, 0x38)[0]
    at = 0x4C
    if flags & HAS_ID_LIST:
        at += 2 + struct.unpack_from('<H', data, at)[0]
    target = ''
    if flags & HAS_LINK_INFO:
        size, header, info_flags, _, base, _, suffix = struct.unpack_from('<7I', data, at)
        wide_base = wide_suffix = 0
        if header >= 0x24:
            wide_base, wide_suffix = struct.unpack_from('<2I', data, at + 28)
        if info_flags & 1:
            if wide_base:
                target = _cstring(data, at + wide_base, True)
                target += _cstring(data, at + wide_suffix, True) if wide_suffix else ''
            else:
                target = _cstring(data, at + base, False) + _cstring(data, at + suffix, False)
        at += size
    wide = bool(flags & IS_UNICODE)
    strings = {}
    for flag in (HAS_NAME, HAS_RELATIVE_PATH, HAS_WORKING_DIR, HAS_ARGUMENTS, HAS_ICON_LOCATION):
        if flags & flag:
            count = struct.unpack_from('<H', data, at)[0]
            at += 2
            width = count * (2 if wide else 1)
            raw = data[at:at + width]
            strings[flag] = raw.decode('utf-16-le' if wide else 'mbcs', 'replace')
            at += width
    while at + 8 <= len(data):                       # extra blocks: an environment-variable target
        size, signature = struct.unpack_from('<2I', data, at)
        if size < 8:
            break
        if signature == ENVIRONMENT_BLOCK and not target and size >= 0x314:
            target = os.path.expandvars(_cstring(data, at + 8 + 260, True) or _cstring(data, at + 8, False))
        at += size
    if not target and flags & HAS_RELATIVE_PATH and path:
        target = os.path.normpath(os.path.join(os.path.dirname(path), strings[HAS_RELATIVE_PATH]))
    icon = os.path.expandvars(strings.get(HAS_ICON_LOCATION, '').strip().strip('"'))
    return Shortcut(path, target, strings.get(HAS_ARGUMENTS, '').strip(), strings.get(HAS_WORKING_DIR, ''),
                    icon, icon_index if icon else 0)


@lru_cache(maxsize=256)
def _read(path: str, stamp: float) -> Shortcut:
    try:
        return parse(Path(path).read_bytes(), path)
    except (OSError, ValueError, struct.error):
        return Shortcut(path)


def read(path: str) -> Shortcut:
    """A shortcut file's contents (cached until the file changes); a plain path is its own target."""
    if not path.lower().endswith('.lnk'):
        return Shortcut(path, path)
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return Shortcut(path)
    return _read(path, stamp)


EXPLORER = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'explorer.exe')


def program_of(path: str) -> str:
    """The program whose windows belong on a pin, lowercase; '' when none can be told.
    File Explorer's own pin names no file (a shell folder), and Explorer windows belong on it."""
    shortcut = read(path)
    target = shortcut.target
    if not target and Path(path).stem.lower() in ('file explorer', 'explorer'):
        target = EXPLORER
    if not target.lower().endswith('.exe'):
        return ''
    return target.lower()


def web_app(path: str) -> bool:
    """A browser's installed web app (its shortcut starts the browser with --app-id): the browser's
    own windows belong elsewhere."""
    arguments = read(path).arguments.lower()
    return '--app-id=' in arguments or '--app=' in arguments


def keep(path: str) -> str:
    """A shortcut to pin, copied into Dockline's own folder so the pin outlives the original (a
    desktop shortcut deleted, a pin taken off the Windows taskbar); other paths are pinned as they are."""
    if not path.lower().endswith('.lnk'):
        return path
    from .config import folder
    pins = folder() / 'Pins'
    source = Path(path)
    try:
        if source.resolve().parent == pins.resolve():
            return path
        pins.mkdir(parents=True, exist_ok=True)
        target = pins / source.name
        number = 2
        while target.exists() and target.read_bytes() != source.read_bytes():
            target = pins / f'{source.stem} ({number}){source.suffix}'
            number += 1
        if not target.exists():
            target.write_bytes(source.read_bytes())
        return str(target)
    except OSError:
        return path


# ---------------------------------------------------------------- the Windows taskbar's pins

def pins_folder() -> Path:
    return Path(os.environ.get('APPDATA') or Path.home()) / 'Microsoft' / 'Internet Explorer' / 'Quick Launch' / \
        'User Pinned' / 'TaskBar'


def taskbar_pins() -> list[str]:
    """The shortcuts pinned to the Windows taskbar, in the taskbar's order. The order is where each
    shortcut's name first appears in Windows' own record of the pins (Taskband\\Favorites); a pin
    not found there goes last. Pins of Store apps that have no shortcut file are not included."""
    folder = pins_folder()
    try:
        files = sorted((p for p in folder.iterdir() if p.suffix.lower() == '.lnk'), key=lambda p: p.name.lower())
    except OSError:
        return []
    record = b''
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband') as key:
            record = bytes(winreg.QueryValueEx(key, 'Favorites')[0] or b'')
    except (OSError, TypeError):
        pass

    def place(p: Path) -> int:
        found = record.find(p.name.encode('utf-16-le')) if record else -1
        return found if found >= 0 else 1 << 30

    return [str(p) for p in sorted(files, key=place)]


def quote(argument: str) -> str:
    """One argument for a Windows command line."""
    if argument and not re.search(r'[\s"]', argument):
        return argument
    return '"' + argument.replace('"', '\\"') + '"'
