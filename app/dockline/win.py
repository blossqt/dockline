"""Windows calls through ctypes: app bars, the taskbar, top-level windows, keys and glass."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field

user32 = ctypes.WinDLL('user32', use_last_error=True)
shell32 = ctypes.WinDLL('shell32')
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
dwmapi = ctypes.WinDLL('dwmapi')

HWND = wintypes.HWND
LRESULT = ctypes.c_ssize_t

# ---------------------------------------------------------------- prototypes

user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = HWND
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.IsWindowVisible.argtypes = [HWND]
user32.IsIconic.argtypes = [HWND]
user32.GetWindow.argtypes = [HWND, wintypes.UINT]
user32.GetWindow.restype = HWND
user32.GetWindowLongPtrW.argtypes = [HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetClassLongPtrW.argtypes = [HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_size_t
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = HWND
user32.SetForegroundWindow.argtypes = [HWND]
user32.BringWindowToTop.argtypes = [HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.PostMessageW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageTimeoutW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT,
                                       wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = LRESULT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [HWND, WNDENUMPROC, wintypes.LPARAM]

# ---------------------------------------------------------------- app bars

class APPBARDATA(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('hWnd', HWND), ('uCallbackMessage', wintypes.UINT),
                ('uEdge', wintypes.UINT), ('rc', wintypes.RECT), ('lParam', wintypes.LPARAM)]


shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
shell32.SHAppBarMessage.restype = ctypes.c_size_t

ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS, ABM_GETSTATE = 0, 1, 2, 3, 4
ABM_SETSTATE = 10
ABE_TOP, ABE_BOTTOM = 1, 3
ABN_STATECHANGE, ABN_POSCHANGED, ABN_FULLSCREENAPP = 0, 1, 2
ABS_AUTOHIDE, ABS_ALWAYSONTOP = 1, 2

APPBAR_MESSAGE = user32.RegisterWindowMessageW('DocklineAppBar')
# Windows' shell hook, as the taskbar gets it: windows created, destroyed, activated, flashing
SHELL_HOOK_MESSAGE = user32.RegisterWindowMessageW('SHELLHOOK')
HSHELL_WINDOWCREATED, HSHELL_WINDOWDESTROYED, HSHELL_WINDOWACTIVATED, HSHELL_REDRAW = 1, 2, 4, 6
HSHELL_RUDEAPPACTIVATED, HSHELL_FLASH = 0x8004, 0x8006
user32.RegisterShellHookWindow.argtypes = [HWND]
user32.DeregisterShellHookWindow.argtypes = [HWND]


class AppBar:
    """The dock as a Windows app bar: Windows keeps maximised windows clear of it and tells it
    when a program goes full screen."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.registered = False

    def _data(self) -> APPBARDATA:
        data = APPBARDATA()
        data.cbSize = ctypes.sizeof(APPBARDATA)
        data.hWnd = self.hwnd
        data.uCallbackMessage = APPBAR_MESSAGE
        return data

    def register(self) -> None:
        if not self.registered:
            self.registered = bool(shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(self._data())))

    def remove(self) -> None:
        if self.registered:
            shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(self._data()))
            self.registered = False

    def place(self, edge: int, screen: tuple[int, int, int, int], thickness: int) -> tuple[int, int, int, int]:
        """Reserve a band of the screen along an edge (physical pixels); returns the band given."""
        self.register()
        data = self._data()
        data.uEdge = edge
        left, top, right, bottom = screen
        data.rc = wintypes.RECT(left, top, right, bottom)
        if edge == ABE_TOP:
            data.rc.bottom = top + thickness
        else:
            data.rc.top = bottom - thickness
        shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(data))
        if edge == ABE_TOP:
            data.rc.bottom = data.rc.top + thickness
        else:
            data.rc.top = data.rc.bottom - thickness
        shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(data))
        return data.rc.left, data.rc.top, data.rc.right, data.rc.bottom


# ---------------------------------------------------------------- the Windows taskbar

def _class_name(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def taskbars() -> list[int]:
    found = []
    main = user32.FindWindowW('Shell_TrayWnd', None)
    if main:
        found.append(main)

    def each(hwnd, _):
        if _class_name(hwnd) == 'Shell_SecondaryTrayWnd':
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(each), 0)
    return found


def _taskbar_data() -> APPBARDATA:
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(APPBARDATA)
    data.hWnd = user32.FindWindowW('Shell_TrayWnd', None)
    return data


def taskbar_state() -> int:
    return int(shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(_taskbar_data())))


def set_taskbar_state(state: int) -> None:
    data = _taskbar_data()
    data.lParam = state
    shell32.SHAppBarMessage(ABM_SETSTATE, ctypes.byref(data))


def show_taskbars(show: bool) -> None:
    for hwnd in taskbars():
        user32.ShowWindow(hwnd, 5 if show else 0)


def taskbar_visible() -> bool:
    hwnd = user32.FindWindowW('Shell_TrayWnd', None)
    return bool(hwnd and user32.IsWindowVisible(hwnd))


START_CLASSES = {'Windows.UI.Core.CoreWindow', 'XamlExplorerHostIslandWindow', 'Shell_TrayWnd',
                 'Shell_SecondaryTrayWnd', 'NotifyIconOverflowWindow', 'TopLevelWindowForOverflowXamlIsland'}


def shell_in_front() -> bool:
    """Start, search, quick settings or the taskbar itself is the foreground window."""
    hwnd = user32.GetForegroundWindow()
    return bool(hwnd) and _class_name(hwnd) in START_CLASSES


# The programs that draw Start, Search and the notification and quick settings panels (Windows 10 and 11)
SHELL_HOSTS = {'startmenuexperiencehost.exe', 'shellexperiencehost.exe', 'shellhost.exe', 'searchhost.exe',
               'searchapp.exe', 'searchui.exe'}
IMMERSIVE_CLASSES = {'Windows.UI.Core.CoreWindow', 'XamlExplorerHostIslandWindow'}
VK_ESCAPE = 0x1B


def window_rect(hwnd) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def shell_over(hwnd) -> int:
    """Start, Search or another of Windows' own panels is the foreground window and covers hwnd: its
    window, or 0. Windows keeps those panels in a layer above every program's always-on-top windows,
    so nothing of the dock's can be shown over them."""
    front = foreground()
    if not front or not hwnd or front == hwnd:
        return 0
    return shell_covering(window_rect(hwnd))


def shell_covering(rect) -> int:
    """Start, Search or another of Windows' own panels is the foreground window over rect (left, top,
    right, bottom, on screen): its window, or 0. For a card or menu about to open there."""
    front = foreground()
    if not front:
        return 0
    program = os.path.basename(process_path(window_pid(front))).lower()
    if program not in SHELL_HOSTS and not (program == 'explorer.exe' and _class_name(front) in IMMERSIVE_CLASSES):
        return 0
    a, b = window_rect(front), rect
    overlaps = max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])
    return front if overlaps else 0


def shell_gone(hwnd) -> bool:
    """Start or Search has been put away: Windows cloaks its window (about 165 ms after Esc here) and
    then plays its slide-away, which takes about as long again."""
    if not hwnd or not user32.IsWindowVisible(hwnd):
        return True
    cloaked = wintypes.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4)
    return bool(cloaked.value)


def dismiss_shell(front) -> bool:
    """Close Start or the like as Esc does, only while that same window is in front: the key must
    never reach anything else, such as a game behind it."""
    if not front or foreground() != front:
        return False
    press(VK_ESCAPE)
    return True


# ---------------------------------------------------------------- top-level windows

GWL_EXSTYLE, GWL_STYLE = -20, -16
WS_CAPTION = 0x00C00000
user32.IsZoomed.argtypes = [HWND]
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
GW_OWNER = 4
DWMWA_CLOAKED = 14
SKIP_CLASSES = {'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd'}


@dataclass
class Window:
    hwnd: int
    title: str
    pid: int
    path: str


@dataclass
class Group:
    """The windows of one program, as the taskbar groups them."""
    path: str
    windows: list[Window] = field(default_factory=list)
    pinned: bool = False


def window_title(hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ''
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def window_pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


_paths: dict[int, str] = {}


def process_path(pid: int) -> str:
    if pid in _paths:
        return _paths[pid]
    handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    path = ''
    if handle:
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                path = buffer.value
        finally:
            kernel32.CloseHandle(handle)
    if len(_paths) > 512:
        _paths.clear()
    _paths[pid] = path
    return path


def _store_app_pid(hwnd, frame_pid: int) -> int:
    """A Store app's frame belongs to ApplicationFrameHost; the app itself owns a child window."""
    found = [frame_pid]

    def each(child, _):
        pid = window_pid(child)
        if pid != frame_pid:
            found[0] = pid
            return False
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(each), 0)
    return found[0]


def app_windows(own_pid: int) -> list[Window]:
    """The windows the taskbar would show, in Windows' z-order."""
    found: list[Window] = []

    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        app = ex & WS_EX_APPWINDOW
        if not app and (ex & WS_EX_TOOLWINDOW or ex & WS_EX_NOACTIVATE or user32.GetWindow(hwnd, GW_OWNER)):
            return True
        title = window_title(hwnd)
        if not title:
            return True
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4)
        if cloaked.value:
            return True
        cls = _class_name(hwnd)
        if cls in SKIP_CLASSES:
            return True
        pid = window_pid(hwnd)
        if pid == own_pid:
            return True
        if cls == 'ApplicationFrameWindow':
            pid = _store_app_pid(hwnd, pid)
        found.append(Window(hwnd, title, pid, process_path(pid)))
        return True

    user32.EnumWindows(WNDENUMPROC(each), 0)
    return found


def window_icon(hwnd) -> int:
    """The icon a window shows in its title bar (an HICON), or 0."""
    for kind in (1, 2, 0):                       # ICON_BIG, ICON_SMALL2, ICON_SMALL
        result = ctypes.c_size_t()
        if user32.SendMessageTimeoutW(hwnd, 0x7F, kind, 0, 0x2, 50, ctypes.byref(result)) and result.value:
            return result.value
    for index in (-14, -34):                     # GCLP_HICON, GCLP_HICONSM
        value = user32.GetClassLongPtrW(hwnd, index)
        if value:
            return value
    return 0


def foreground() -> int:
    return user32.GetForegroundWindow() or 0


def is_minimised(hwnd) -> bool:
    return bool(user32.IsIconic(hwnd))


def activate(hwnd) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)               # SW_RESTORE
    if user32.SetForegroundWindow(hwnd):
        return
    front = user32.GetForegroundWindow()
    other = user32.GetWindowThreadProcessId(front, None) if front else 0
    mine = kernel32.GetCurrentThreadId()
    if other and other != mine:
        user32.AttachThreadInput(mine, other, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(mine, other, False)


def minimise(hwnd) -> None:
    user32.ShowWindow(hwnd, 6)                   # SW_MINIMIZE


def close(hwnd) -> None:
    user32.PostMessageW(hwnd, 0x10, 0, 0)        # WM_CLOSE


SC_SIZE, SC_MOVE, SC_MINIMIZE, SC_MAXIMIZE, SC_CLOSE, SC_RESTORE = 0xF000, 0xF010, 0xF020, 0xF030, 0xF060, 0xF120
WS_THICKFRAME, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x40000, 0x20000, 0x10000


def window_commands(hwnd) -> list[tuple[str, int, bool]]:
    """A window's own menu (its title bar's, as the taskbar's previews show it on a right-click): each
    item's name, its command (WM_SYSCOMMAND) and whether the window's state allows it now. Restore,
    Move and Size are greyed while it is as it was; Minimize and Maximize follow its title-bar buttons."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    iconic, zoomed = bool(user32.IsIconic(hwnd)), bool(user32.IsZoomed(hwnd))
    normal = not iconic and not zoomed
    return [('Restore', SC_RESTORE, not normal), ('Move', SC_MOVE, normal),
            ('Size', SC_SIZE, normal and bool(style & WS_THICKFRAME)),
            ('Minimize', SC_MINIMIZE, not iconic and bool(style & WS_MINIMIZEBOX)),
            ('Maximize', SC_MAXIMIZE, not zoomed and bool(style & WS_MAXIMIZEBOX)),
            ('Close', SC_CLOSE, True)]


def window_command(hwnd, command: int) -> None:
    """Carry out an item of a window's own menu, as choosing it there does. Move and Size work with the
    keyboard on the window, so it comes to the front first; Minimize and Close need no focus."""
    if command in (SC_MOVE, SC_SIZE, SC_RESTORE, SC_MAXIMIZE):
        activate(hwnd)
    user32.PostMessageW(hwnd, 0x0112, command, 0)    # WM_SYSCOMMAND


def no_activate(hwnd) -> None:
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


# ---------------------------------------------------------------- keys

VK_LWIN, VK_SHIFT = 0x5B, 0x10
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB3
_EXTENDED = {VK_LWIN, VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY_PAUSE}


VK_CONTROL = 0x11


def task_manager() -> None:
    """Open Task Manager as the taskbar's menu does: Ctrl+Shift+Esc, which Windows answers itself, so
    no elevation prompt and no second copy if it is open already."""
    press(VK_CONTROL, VK_SHIFT, VK_ESCAPE)


def press(*keys: int) -> None:
    """Press keys together, then let them go in reverse order."""
    for key in keys:
        user32.keybd_event(key, 0, 1 if key in _EXTENDED else 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, (1 if key in _EXTENDED else 0) | 2, 0)


# ---------------------------------------------------------------- glass and corners

class _AccentPolicy(ctypes.Structure):
    _fields_ = [('AccentState', ctypes.c_int), ('AccentFlags', ctypes.c_int),
                ('GradientColor', ctypes.c_uint), ('AnimationId', ctypes.c_int)]


class _CompositionData(ctypes.Structure):
    _fields_ = [('Attribute', ctypes.c_int), ('Data', ctypes.c_void_p), ('SizeOfData', ctypes.c_size_t)]


def acrylic(hwnd: int, on: bool, tint_abgr: int = 0x30141418) -> bool:
    """Windows' acrylic blur behind a window (on), or none."""
    try:
        function = user32.SetWindowCompositionAttribute
    except AttributeError:
        return False
    function.argtypes = [HWND, ctypes.POINTER(_CompositionData)]
    accent = _AccentPolicy(4 if on else 0, 2 if on else 0, tint_abgr, 0)
    data = _CompositionData(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
    return bool(function(hwnd, ctypes.byref(data)))


def round_corners(hwnd: int, on: bool) -> None:
    value = ctypes.c_int(2 if on else 1)         # DWMWCP_ROUND, DWMWCP_DONOTROUND
    dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(value), 4)


def glass_available() -> bool:
    import sys
    return sys.getwindowsversion().build >= 17134


# ---------------------------------------------------------------- the end of the process

def terminate(code: int) -> None:
    """End at once, as the suite's apps do: Qt's teardown can hang or crash."""
    kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    os._exit(code)


# ---------------------------------------------------------------- full-screen apps

class _MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT),
                ('dwFlags', wintypes.DWORD)]


user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.MonitorFromWindow.argtypes = [HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]

DESKTOP_CLASSES = {'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd'}


def full_screen_in_front(own_pid: int, device: str | None = None) -> bool:
    """A window covering a whole screen is in front - the main one, or the display named - a game or
    a video, not the desktop, Start or one of the dock's own windows."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = _class_name(hwnd)
    if cls in DESKTOP_CLASSES or cls in START_CLASSES or window_pid(hwnd) == own_pid:
        return False
    if user32.IsZoomed(hwnd):
        return False                     # maximised (a browser, an editor): with the taskbar hidden it also
                                         # covers the screen, but the dock must still come up over it
    if user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & WS_CAPTION == WS_CAPTION:
        return False                     # a window with a title bar is not a game or a full-screen video
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    monitor = user32.MonitorFromWindow(hwnd, 2)              # MONITOR_DEFAULTTONEAREST
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    if device is None and not info.dwFlags & 1:
        return False                                         # not on the main screen, where the dock is
    if device is not None and monitor_name(hwnd) != device:
        return False
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    screen = info.rcMonitor
    return (rect.left <= screen.left and rect.top <= screen.top and rect.right >= screen.right
            and rect.bottom >= screen.bottom)


kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD


def wait_for_exit(pid: int) -> bool:
    """Wait until a process ends; False if it could not be watched."""
    handle = kernel32.OpenProcess(0x00100000, False, pid)      # SYNCHRONIZE
    if not handle:
        return False
    try:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        return True
    finally:
        kernel32.CloseHandle(handle)


def remove_app_bar(hwnd: int) -> None:
    """Give back the screen band a dock that ended without quitting had reserved."""
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(APPBARDATA)
    data.hWnd = hwnd
    shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(data))


# ---------------------------------------------------------------- monitors and Windows' own taskbar settings

class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT),
                ('dwFlags', wintypes.DWORD), ('szDevice', ctypes.c_wchar * 32)]


def monitor_name(hwnd) -> str:
    """The display a window is on, by Windows' device name (as Qt names its screens)."""
    monitor = user32.MonitorFromWindow(hwnd, 2)
    info = _MonitorInfoEx()
    info.cbSize = ctypes.sizeof(info)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.cast(ctypes.pointer(info), ctypes.POINTER(_MonitorInfo))):
        return ''
    return info.szDevice


_ADVANCED = r'Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'


_settings_cache: list = [0.0, (True, 0)]


def taskbar_settings() -> tuple[bool, int]:
    """taskbar_settings_now(), read again at most every 3 seconds (it is asked for every window)."""
    import time
    if time.monotonic() - _settings_cache[0] > 3:
        _settings_cache[:] = [time.monotonic(), taskbar_settings_now()]
    return _settings_cache[1]


def taskbar_settings_now() -> tuple[bool, int]:
    """Windows' Settings > Personalization > Taskbar > Taskbar behaviors: whether the taskbar shows on
    all displays, and where its apps show - 0 all taskbars, 1 the main taskbar and the one where the
    window is open, 2 the taskbar where the window is open."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ADVANCED) as key:
            def value(name, default):
                try:
                    return int(winreg.QueryValueEx(key, name)[0])
                except OSError:
                    return default
            return bool(value('MMTaskbarEnabled', 1)), value('MMTaskbarMode', 0)   # unset means on, as Windows 11's default
    except OSError:
        return True, 0


def left_button_down() -> bool:
    return bool(user32.GetAsyncKeyState(0x01) & 0x8000)


def dragging_files() -> bool:
    """A file is being dragged somewhere on screen: Windows draws its picture in a window of its own."""
    hwnd = user32.FindWindowW('SysDragImage', None)
    return bool(hwnd and user32.IsWindowVisible(hwnd)) and left_button_down()


# ---------------------------------------------------------------- starting and ending programs

shell32.ShellExecuteW.argtypes = [HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_ssize_t


def shell_open(program: str, arguments: str = '', folder: str | None = None, verb: str = 'open') -> bool:
    """Start a program (or open a file) with arguments, as Explorer would ('runas': as administrator)."""
    return shell32.ShellExecuteW(None, verb, program, arguments or None, folder or None, 1) > 32


def end_process(pid: int) -> bool:
    """End a program at once (the taskbar's End task)."""
    handle = kernel32.OpenProcess(0x0001, False, pid)          # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------- keyboard languages and input methods

imm32 = ctypes.WinDLL('imm32')
imm32.ImmGetDefaultIMEWnd.argtypes = [HWND]
imm32.ImmGetDefaultIMEWnd.restype = HWND
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
user32.GetKeyboardLayoutList.restype = ctypes.c_int
kernel32.LCIDToLocaleName.argtypes = [wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD]
kernel32.GetLocaleInfoEx.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int]

WM_INPUTLANGCHANGEREQUEST, WM_IME_CONTROL = 0x50, 0x283
IMC_GETCONVERSIONMODE, IMC_SETCONVERSIONMODE, IMC_GETOPENSTATUS, IMC_SETOPENSTATUS = 1, 2, 5, 6
LOCALE_SLOCALIZEDDISPLAYNAME, LOCALE_SISO639LANGNAME2, LOCALE_SISO3166CTRYNAME = 0x2, 0x67, 0x5A
LOCALE_SNATIVELANGUAGENAME = 0x4


def keyboard_of(hwnd) -> int:
    """The keyboard layout (HKL) a window's thread types with."""
    thread = user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
    return int(user32.GetKeyboardLayout(thread) or 0)


def keyboards() -> list[int]:
    """Every keyboard layout set up in Windows, in its order."""
    count = user32.GetKeyboardLayoutList(0, None)
    if count <= 0:
        return []
    found = (ctypes.c_void_p * count)()
    count = user32.GetKeyboardLayoutList(count, found)
    return [int(value or 0) for value in found[:count]]


def locale_of(hkl: int) -> str:
    """The language a layout is for, as a locale name ('en-US', 'zh-CN')."""
    buffer = ctypes.create_unicode_buffer(85)
    if kernel32.LCIDToLocaleName(hkl & 0xFFFF, buffer, 85, 0):
        return buffer.value
    return ''


def locale_info(name: str, kind: int) -> str:
    buffer = ctypes.create_unicode_buffer(128)
    if name and kernel32.GetLocaleInfoEx(name, kind, buffer, 128):
        return buffer.value
    return ''


def _ime_ask(ime, what: int, value: int = 0):
    result = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(ime, WM_IME_CONTROL, what, value, 0x2, 60, ctypes.byref(result)):
        return None
    return int(result.value)


def ime_native(hwnd) -> bool | None:
    """Whether a window's input method types its own language (Chinese) rather than Latin letters
    (English mode); None when it has no input method that says."""
    ime = imm32.ImmGetDefaultIMEWnd(hwnd) if hwnd else None
    if not ime:
        return None
    opened = _ime_ask(ime, IMC_GETOPENSTATUS)
    mode = _ime_ask(ime, IMC_GETCONVERSIONMODE)
    if opened is None or mode is None:
        return None
    return bool(opened) and bool(mode & 1)                    # IME_CMODE_NATIVE


def set_ime_native(hwnd, native: bool) -> None:
    ime = imm32.ImmGetDefaultIMEWnd(hwnd) if hwnd else None
    if not ime:
        return
    mode = _ime_ask(ime, IMC_GETCONVERSIONMODE) or 0
    if native:
        _ime_ask(ime, IMC_SETOPENSTATUS, 1)
    _ime_ask(ime, IMC_SETCONVERSIONMODE, (mode | 1) if native else (mode & ~1))


def switch_keyboard(hwnd, hkl: int) -> None:
    """Ask a window to type with another layout, as the language switcher does."""
    if hkl >= 1 << 63:
        hkl -= 1 << 64                                        # LPARAM is signed
    user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl)


# ---------------------------------------------------------------- a framed window's look

class _Margins(ctypes.Structure):
    _fields_ = [('left', ctypes.c_int), ('right', ctypes.c_int), ('top', ctypes.c_int), ('bottom', ctypes.c_int)]


dwmapi.DwmExtendFrameIntoClientArea.argtypes = [HWND, ctypes.POINTER(_Margins)]


def framed_look(hwnd: int, dark: bool, blur: bool) -> None:
    """A framed window's title bar dark or light, and (blur) Windows' acrylic behind all of it."""
    value = ctypes.c_int(1 if dark else 0)
    dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), 4)          # DWMWA_USE_IMMERSIVE_DARK_MODE
    if blur:
        margins = _Margins(-1, -1, -1, -1)
        dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        kind = ctypes.c_int(3)                                               # DWMSBT_TRANSIENTWINDOW: acrylic
        dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(kind), 4)       # DWMWA_SYSTEMBACKDROP_TYPE


# ---------------------------------------------------------------- a program's own name

_version = ctypes.WinDLL('version')
_version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
_version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                    ctypes.POINTER(wintypes.UINT)]
_descriptions: dict[str, str] = {}


def program_name(path: str) -> str:
    """A program's name as it gives it (its file description: 'Steam', 'Microsoft Edge'), or its file
    name's stem."""
    if path in _descriptions:
        return _descriptions[path]
    name = ''
    try:
        size = _version.GetFileVersionInfoSizeW(path, None)
        if size:
            data = ctypes.create_string_buffer(size)
            if _version.GetFileVersionInfoW(path, 0, size, data):
                pointer, length = ctypes.c_void_p(), wintypes.UINT()
                languages = []
                if _version.VerQueryValueW(data, r'\VarFileInfo\Translation', ctypes.byref(pointer),
                                           ctypes.byref(length)) and length.value >= 4:
                    words = (ctypes.c_ushort * (length.value // 2)).from_address(pointer.value)
                    languages = [f'{words[i]:04x}{words[i + 1]:04x}' for i in range(0, len(words) - 1, 2)]
                for language in languages + ['040904b0', '040904e4']:
                    if _version.VerQueryValueW(data, '\\StringFileInfo\\' + language + '\\FileDescription',
                                               ctypes.byref(pointer), ctypes.byref(length)) and length.value > 1:
                        name = ctypes.wstring_at(pointer.value, length.value - 1).strip()
                        if name:
                            break
    except OSError:
        name = ''
    if not name:
        stem = os.path.splitext(os.path.basename(path))[0]
        name = stem[:1].upper() + stem[1:]
    if len(_descriptions) > 256:
        _descriptions.clear()
    _descriptions[path] = name
    return name
