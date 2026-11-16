# Dockline

A dock for Windows 11 that can take the taskbar's place: pinned and open apps with live previews,
and widgets for the clock, weather, sound, system load, timers and more.

Part of the blossqt suite. Hanami lists Dockline once it is installed; it is not offered for
installing.

- Runs on Windows 10 and 11 (64-bit).
- Build: `powershell -ExecutionPolicy Bypass -File build\bootstrap.ps1`, then
  `powershell -ExecutionPolicy Bypass -File build\build_app.ps1`.
- `Dockline.exe --restore-taskbar` gives the Windows taskbar back after an unexpected exit.

`app/dockline/suitetheme.py` and `suitelink.py` are copies of Hanami's originals.
