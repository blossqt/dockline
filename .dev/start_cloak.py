"""Opens Start, closes it with Esc (only while Start or Search is in front), and prints, every 10 ms,
whether its window is still the foreground one, visible and not cloaked - to find when it is gone."""
import ctypes
import os
import time
from ctypes import wintypes

u, k, dwm = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.dwmapi
u.GetForegroundWindow.restype = wintypes.HWND
SHELL = {'startmenuexperiencehost.exe', 'searchhost.exe'}


def program(hwnd):
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k.OpenProcess(0x1000, False, pid.value)
    buf = ctypes.create_unicode_buffer(520)
    n = wintypes.DWORD(520)
    if h:
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        k.CloseHandle(h)
    return os.path.basename(buf.value).lower()


def cloaked(hwnd):
    value = ctypes.c_int()
    dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(value), 4)
    return value.value


def key(vk):
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, 2, 0)


key(0x5B)
time.sleep(1.0)
front = u.GetForegroundWindow()
if program(front) not in SHELL:
    raise SystemExit(f'Start is not in front: {program(front)}')
print('Start window', hex(front or 0), program(front))
key(0x1B)
start = time.perf_counter()
last = None
while time.perf_counter() - start < 0.8:
    state = (u.GetForegroundWindow() == front, bool(u.IsWindowVisible(front)), cloaked(front))
    if state != last:
        print(f'{(time.perf_counter() - start) * 1000:6.0f} ms  front={state[0]} visible={state[1]} cloaked={state[2]}')
        last = state
    time.sleep(0.005)
