# PyInstaller spec for Dockline - run by build/build.py
import os

app_dir = os.path.abspath(os.path.join(SPECPATH, '..', 'app'))    # the code: main.py and the dockline package

# Qt's plugins, named here rather than left to PyInstaller's Qt hook: a build run from inside a
# packaged app (the Claude desktop app) sees AppData by two paths, and the hook then drops every
# plugin, qwindows.dll included, so the exe cannot open a window. (As Grabbit's spec does.)
import PySide6
qt_plugins = os.path.join(os.path.dirname(PySide6.__file__), 'plugins')
qt_plugin_binaries = [
    (os.path.join(qt_plugins, kind, name), os.path.join('PySide6', 'plugins', kind))
    for kind in ('platforms', 'imageformats', 'iconengines', 'styles', 'generic')
    if os.path.isdir(os.path.join(qt_plugins, kind))
    for name in sorted(os.listdir(os.path.join(qt_plugins, kind)))
    if name.lower().endswith('.dll')
]
if not any(dest.endswith('platforms') and src.endswith('qwindows.dll') for src, dest in qt_plugin_binaries):
    raise SystemExit("Qt's Windows platform plugin (qwindows.dll) was not found - the exe could not start")

a = Analysis(
    [os.path.join(app_dir, 'main.py')],
    pathex=[app_dir],
    binaries=qt_plugin_binaries,
    datas=[],
    hiddenimports=['dockline', 'dockline.app', 'dockline.dock', 'dockline.widgets', 'dockline.picker', 'dockline.glassfix',
                   'dockline.settings_window', 'dockline.media', 'dockline.ai', 'PySide6.QtNetwork',
                   'winrt.runtime', 'winrt.system', 'winrt.windows.foundation',
                   'winrt.windows.foundation.collections', 'winrt.windows.media.control',
                   'winrt.windows.storage.streams'],
    hookspath=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc_data', 'lib2to3', 'test',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.Qt3DCore',
        'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtTest', 'PySide6.QtSql',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtCharts',
        'PySide6.QtMultimedia', 'PySide6.QtOpenGL', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Dockline',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.environ.get('DOCKLINE_CONSOLE') == '1',     # a console build shows start-up tracebacks
    disable_windowed_traceback=False,
    icon=os.path.join(SPECPATH, 'dockline.ico'),
    version=os.path.join(SPECPATH, 'version_info.txt'),
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name='Dockline')
