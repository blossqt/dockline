# Dockline

A dock for Windows 11 that can take the taskbar's place: pinned and open apps with live previews,
and widgets for the clock, weather, sound, system load, timers and more.

Part of the blossqt suite: Hanami installs and updates it.

What it does in the taskbar's place:

- Pinned apps (the Windows taskbar's pins are brought over at first start, and from Settings) and
  open windows, grouped by program; a program's windows show on its pinned shortcut.
- Previews on hover, attention flashing, middle-click or Shift+click for a new window, End task.
- Files dragged onto an app open in it; held over an open app, its window comes forward. Programs
  and shortcuts dragged onto the dock are pinned. A hidden dock comes up for a file drag.
- The input language of the window in front, with the input mode of Chinese, Japanese and Korean
  input methods (shown where Windows has more than one keyboard language).
- Hides only for games and full-screen video; a maximised window keeps it reachable.

- Runs on Windows 10 and 11 (64-bit).
- Build: `powershell -ExecutionPolicy Bypass -File build\bootstrap.ps1`, then
  `powershell -ExecutionPolicy Bypass -File build\build_app.ps1`.
- `Dockline.exe --restore-taskbar` gives the Windows taskbar back after an unexpected exit.

`app/dockline/suitetheme.py` and `suitelink.py` are copies of Hanami's originals.
