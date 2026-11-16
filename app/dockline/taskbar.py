"""Hiding the Windows taskbar while the dock runs, and always giving it back.

Hiding sets the taskbar to auto-hide (so it reserves no space) and hides its window. The state it
had before is saved in the settings until it is restored, so a dock that crashed or was ended
puts the taskbar back the next time it starts (or with --restore-taskbar).
"""

from __future__ import annotations

import time

from . import win


class Taskbar:
    def __init__(self, cfg):
        self.cfg = cfg
        self.hold_until = 0.0
        self.found: list[int] = []                  # the taskbars' windows (the main one and each display's)
        self.found_at = 0.0

    def hold(self, seconds: float) -> None:
        """Leave the taskbar shown for a while (the hidden icons open from it)."""
        self.hold_until = time.monotonic() + seconds

    def hide(self) -> None:
        if self.cfg['taskbar_saved'] is None:
            self.cfg['taskbar_saved'] = win.taskbar_state()
            self.cfg.save()
        win.set_taskbar_state(win.ABS_AUTOHIDE | (self.cfg['taskbar_saved'] & win.ABS_ALWAYSONTOP))
        win.show_taskbars(False)

    def restore(self) -> None:
        from .veil import clear_taskbars
        clear_taskbars(False)                        # never leave it see-through from a tray opening
        saved = self.cfg['taskbar_saved']
        if saved is not None:
            win.set_taskbar_state(saved)
            self.cfg['taskbar_saved'] = None
            self.cfg.save()
        win.show_taskbars(True)

    def keep_hidden(self) -> None:
        """Windows shows a taskbar again - Start, some shortcuts, or a pointer at the bottom of another
        display, where its second taskbar sits - so every taskbar is looked at often (cheap: a check
        of each window's visibility) and hidden as soon as it shows, unless Start or the like is open."""
        if not self.cfg['hide_taskbar'] or time.monotonic() < self.hold_until:
            return
        now = time.monotonic()
        if now - self.found_at > 5 or not self.found:
            self.found = win.taskbars()              # displays come and go: looked up again now and then
            self.found_at = now
        shown = [hwnd for hwnd in self.found if win.user32.IsWindowVisible(hwnd)]
        if shown and not win.shell_in_front():
            for hwnd in shown:
                win.user32.ShowWindow(hwnd, 0)

    def recover(self) -> None:
        """At start: undo a hide left over from a run that ended without restoring it."""
        if self.cfg['taskbar_saved'] is not None and not self.cfg['hide_taskbar']:
            self.restore()
