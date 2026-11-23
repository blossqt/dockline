"""Drives the running Dockline with real input, step by step, for testing while the user is away.
Every click is checked first to land on a window of Dockline's (or of the shell's, where a step says
so); keys are only sent while an expected window is in front, never into anything else (Esc into the
Claude app would stop the session driving this). Pictures go to .dev\\out\\live_<name>.png.

    python .dev\\live.py "move 690 1048" "wait 0.8" "shot previews" ...

Steps:  move X Y [glide_ms]        pointer there (gliding in 4 ms steps if given)
        click X Y [left|right]     a click, only onto Dockline's windows
        shellclick X Y             a click onto Dockline while Start or Search is in front
        win                        the Windows key (opens or closes Start)
        esc                        Esc, only while Dockline, Start or Search is in front
        drag X1 Y1 X2 Y2 ms [hold] left button down, glide, (hold s), up - only from Dockline
        wait S
        shot NAME [x y w h]        a picture of the screen (the main one, or that part)
        front                      prints the window in front
        frames NAME x y w h S      pictures of that part as fast as they come for S seconds; prints
                                   the gaps between changes (hitches) and saves the frames' strip
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
u, k = ctypes.windll.user32, ctypes.windll.kernel32
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
u.WindowFromPoint.restype = wintypes.HWND
u.WindowFromPoint.argtypes = [wintypes.POINT]
u.GetAncestor.restype = wintypes.HWND
u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
u.GetForegroundWindow.restype = wintypes.HWND

from PySide6.QtCore import Qt                  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPixmap   # noqa: E402

app = QGuiApplication([])
OUT = Path(__file__).with_name('out')
OUT.mkdir(exist_ok=True)
SHELL = {'startmenuexperiencehost.exe', 'searchhost.exe', 'shellexperiencehost.exe', 'shellhost.exe',
         'searchapp.exe'}


def program(hwnd) -> str:
    if not hwnd:
        return ''
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k.OpenProcess(0x1000, False, pid.value)
    buf = ctypes.create_unicode_buffer(520)
    n = wintypes.DWORD(520)
    if h:
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        k.CloseHandle(h)
    return os.path.basename(buf.value).lower()


def under(x, y) -> str:
    hwnd = u.WindowFromPoint(wintypes.POINT(x, y))
    return program(u.GetAncestor(hwnd, 2) if hwnd else 0)


def front() -> str:
    return program(u.GetForegroundWindow())


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]
    _anonymous_ = ('u',)
    _fields_ = [('type', wintypes.DWORD), ('u', _U)]


def send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    u.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def mouse(flags):
    i = INPUT(type=0)
    i.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
    return i


def key(vk, up=False):
    i = INPUT(type=1)
    i.ki = KEYBDINPUT(vk, 0, 0x2 if up else 0, 0, 0)
    return i


BUTTONS = {'left': (0x2, 0x4), 'right': (0x8, 0x10)}


def glide(x, y, ms):
    pt = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    steps = max(1, int(ms / 4))
    for i in range(1, steps + 1):
        u.SetCursorPos(round(pt.x + (x - pt.x) * i / steps), round(pt.y + (y - pt.y) * i / steps))
        time.sleep(0.004)


def shot(name, x=None, y=None, w=None, h=None):
    screen = QGuiApplication.primaryScreen()
    g = screen.geometry()
    if x is None:
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
    screen.grabWindow(0, x, y, w, h).save(str(OUT / f'live_{name}.png'))
    print('shot', name, f'(front: {front()})')


def frames(name, x, y, w, h, seconds):
    """Pictures as fast as GDI gives them; the times between pictures that differ show the motion's
    pace, and a long gap mid-motion is a hitch."""
    screen = QGuiApplication.primaryScreen()
    got = []
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        t = time.perf_counter()
        got.append((t, screen.grabWindow(0, x, y, w, h).toImage()))
    changes = [got[0][0]]
    for (t0, a), (t1, b) in zip(got, got[1:]):
        if a != b:
            changes.append(t1)
    gaps = [round((b - a) * 1000, 1) for a, b in zip(changes, changes[1:])]
    grab_ms = (got[-1][0] - got[0][0]) / max(1, len(got) - 1) * 1000
    print(f'frames {name}: {len(got)} pictures ({grab_ms:.1f} ms each), {len(changes) - 1} changes')
    if gaps:
        print('  gaps between changes (ms):', gaps[:80])
        print(f'  longest {max(gaps)} ms; median {sorted(gaps)[len(gaps) // 2]} ms')
    keep = [img for i, (t, img) in enumerate(got) if i % max(1, len(got) // 12) == 0][:12]
    strip = QImage(w, h * len(keep), QImage.Format_RGB32)
    p = QPainter(strip)
    for i, img in enumerate(keep):
        p.drawImage(0, i * h, img)
    p.end()
    strip.save(str(OUT / f'live_{name}_frames.png'))


def run(step: str):
    words = step.split()
    cmd, args = words[0], words[1:]
    if cmd == 'move':
        x, y = int(args[0]), int(args[1])
        if len(args) > 2:
            glide(x, y, float(args[2]))
        else:
            u.SetCursorPos(x, y)
    elif cmd in ('click', 'shellclick'):
        x, y = int(args[0]), int(args[1])
        button = args[2] if len(args) > 2 else 'left'
        target = under(x, y)
        if target != 'dockline.exe':
            print(f'! not clicking ({x},{y}): {target or "nothing"} is there')
            return False
        if cmd == 'shellclick' and front() not in SHELL:
            print(f'! not clicking: {front()} is in front, not Start or Search')
            return False
        u.SetCursorPos(x, y)
        time.sleep(0.05)
        down, up = BUTTONS[button]
        send(mouse(down))
        time.sleep(0.06)
        send(mouse(up))
    elif cmd == 'win':
        send(key(0x5B), key(0x5B, True))
    elif cmd == 'esc':
        if front() not in SHELL | {'dockline.exe'}:
            print(f'! no Esc: {front()} is in front')
            return False
        send(key(0x1B), key(0x1B, True))
    elif cmd == 'drag':
        x1, y1, x2, y2, ms = int(args[0]), int(args[1]), int(args[2]), int(args[3]), float(args[4])
        hold = float(args[5]) if len(args) > 5 else 0.0
        if under(x1, y1) != 'dockline.exe':
            print(f'! not dragging from ({x1},{y1}): {under(x1, y1)}')
            return False
        u.SetCursorPos(x1, y1)
        time.sleep(0.05)
        send(mouse(0x2))
        time.sleep(0.05)
        glide(x2, y2, ms)
        if hold:
            time.sleep(hold)
        send(mouse(0x4))
    elif cmd == 'hclick':                  # a click onto Hanami's window alone (its Update button)
        x, y = int(args[0]), int(args[1])
        if under(x, y) != 'hanami.exe':
            print(f'! not clicking ({x},{y}): {under(x, y) or "nothing"} is there')
            return False
        u.SetCursorPos(x, y)
        time.sleep(0.05)
        send(mouse(0x2))
        time.sleep(0.06)
        send(mouse(0x4))
    elif cmd == 'down':                    # the left button held, only on Dockline
        x, y = int(args[0]), int(args[1])
        if under(x, y) != 'dockline.exe':
            print(f'! not pressing ({x},{y}): {under(x, y)}')
            return False
        u.SetCursorPos(x, y)
        time.sleep(0.05)
        send(mouse(0x2))
    elif cmd == 'up':
        send(mouse(0x4))
    elif cmd == 'wait':
        time.sleep(float(args[0]))
    elif cmd == 'shot':
        shot(args[0], *map(int, args[1:]))
    elif cmd == 'front':
        print('front:', front())
    elif cmd == 'frames':
        frames(args[0], *map(int, args[1:5]), float(args[5]))
    return True


if __name__ == '__main__':
    for s in [a for a in sys.argv[1:] if not a.startswith("--")]:
        if run(s) is False and '--go-on' not in sys.argv:
            break
