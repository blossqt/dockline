"""Build Dockline.exe into dist\\Dockline, as the suite's apps are built.

    python build/build.py

Runs with a Python that has PySide6 and PyInstaller: DOCKLINE_PYTHON if set, else Dockline's own
build venv (%LOCALAPPDATA%\\DocklineBuild\\venv), else Grabbit's, which has the same two. Steps:
an import check (so a syntax error never reaches the windowed exe), the icon from the app's own
drawing, PyInstaller with the spec, then the built exe's --self-test.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # the repository: app, build and dist folders
APP = ROOT / 'app'                       # the code: main.py and the dockline package
DIST = ROOT / 'dist'
WORK = Path(tempfile.gettempdir()) / 'dockline-build'
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def build_python() -> str | None:
    local = os.environ.get('LOCALAPPDATA', '')
    for candidate in (os.environ.get('DOCKLINE_PYTHON'),
                      os.path.join(local, 'DocklineBuild', 'venv', 'Scripts', 'python.exe'),
                      os.path.join(local, 'GrabbitBuild', 'venv', 'Scripts', 'python.exe')):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def has_tools() -> bool:
    try:
        import PyInstaller  # noqa: F401
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def step(text: str) -> None:
    print(f'== {text}', flush=True)


def write_icon(out: Path) -> None:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    sys.path.insert(0, str(APP))
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication

    from dockline.look import render_app_icon
    app = QApplication.instance() or QApplication([])
    images = []
    for size in ICO_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        render_app_icon(size).save(buffer, 'PNG')
        images.append((size, bytes(buffer.data())))
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = blobs = b''
    for size, data in images:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out.write_bytes(header + entries + blobs)
    app.quit()
    print(f'wrote {out.name} ({len(images)} sizes)')


def write_version(out: Path, version: str) -> None:
    parts = (version.split('.') + ['0', '0', '0', '0'])[:4]
    numbers = ', '.join(str(int(p)) for p in parts)
    out.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numbers}), prodvers=({numbers}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'blossqt'), StringStruct('FileDescription', 'Dockline'),
      StringStruct('FileVersion', '{version}'), StringStruct('ProductName', 'Dockline'),
      StringStruct('ProductVersion', '{version}'), StringStruct('OriginalFilename', 'Dockline.exe')])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding='utf-8')


def running_from_dist() -> bool:
    exe = str(DIST / 'Dockline' / 'Dockline.exe').lower()
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              "Get-Process Dockline -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }"],
                             capture_output=True, text=True, timeout=30,
                             creationflags=0x08000000).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return exe in out


def main() -> int:
    if not has_tools():
        python = build_python()
        if python is None:
            print('No Python with PySide6 and PyInstaller was found. Set DOCKLINE_PYTHON to one.')
            return 2
        return subprocess.call([python, str(Path(__file__).resolve())])

    if running_from_dist():
        print('Dockline is running from dist\\Dockline. Quit it (right-click the dock > Quit Dockline) and '
              'build again.')
        return 3

    step('import check')
    check = subprocess.run([sys.executable, '-c', 'import dockline.app, dockline.dock, dockline.widgets, '
                            'dockline.picker, dockline.settings_window'], cwd=APP,
                           env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
    if check.returncode:
        print('The import check failed.')
        return check.returncode

    sys.path.insert(0, str(APP))
    from dockline import __version__

    step('icon and version')
    write_icon(HERE / 'dockline.ico')
    write_version(HERE / 'version_info.txt', __version__)

    step('PyInstaller')
    shutil.rmtree(DIST / 'Dockline', ignore_errors=True)
    result = subprocess.run([sys.executable, '-m', 'PyInstaller', str(HERE / 'dockline.spec'), '--noconfirm',
                             '--distpath', str(DIST), '--workpath', str(WORK), '--log-level', 'WARN'], cwd=APP)
    if result.returncode:
        print('PyInstaller failed.')
        return result.returncode

    step('self-test of the built exe')
    exe = DIST / 'Dockline' / 'Dockline.exe'
    test = subprocess.run([str(exe), '--self-test'], cwd=exe.parent, timeout=180,
                          env={**os.environ, 'DOCKLINE_SETTINGS': str(WORK / 'self-test-settings.json')})
    if test.returncode:
        print(f'The built exe failed its self-test (exit {test.returncode}).')
        return 1
    size = sum(f.stat().st_size for f in exe.parent.rglob('*') if f.is_file()) / 2 ** 20
    print(f'Built Dockline {__version__}: {exe} ({size:.0f} MB). Self-test passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
