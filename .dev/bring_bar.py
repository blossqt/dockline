"""Brings the running Dockline's hidden bar up as a person does - the pointer to the bottom edge of
the main screen - and holds it there for a moment. Only the pointer moves; nothing is clicked.
    python .dev\\bring_bar.py [seconds]"""
import ctypes
import sys
import time

u = ctypes.windll.user32
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
u.SetCursorPos(w // 2, h - 1)
time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 0.8)
u.SetCursorPos(w // 2, h - 30)       # onto the bar, in its middle: it stays while the pointer is on it
time.sleep(0.4)
print('pointer at', w // 2, h - 30)
