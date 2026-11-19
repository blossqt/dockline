# Dockline

A dock for Windows 11 that can take the taskbar's place: pinned and open apps with live previews,
and widgets for the clock, weather, sound, system load, timers and more.

Part of the blossqt suite: Hanami installs and updates it.

What it does in the taskbar's place:

- Pinned apps (the Windows taskbar's pins are brought over at first start, and from Settings) and
  open windows, grouped by program; a program's windows show on its pinned shortcut.
- Search opens Windows search. Media is one button: its card has a page per app playing sound,
  with art, title and controls, turned with the scroll wheel, as Android's media player.
- Volume per app in the Sound card; a draggable progress bar on each media page.
- AI usage trackers (AI Limits, Claude, ChatGPT and Gemini) showing remaining allowance and reset timers from your existing local sign-ins, with live updates as you use them.
- Settings > Backup exports and imports every setting, pinned shortcuts included.
- Previews on hover (and on a click on an app with several windows), close above the dock; resting on
  one shows that window alone (Windows' own live preview). Moving along the dock, they glide from app
  to app; in the Glass style they are frosted like the cards. Start or Search covering them closes, so
  the dock brought up over a full-screen game with Start can switch windows. Attention flashing, middle-click or Shift+click for a new window, End task.
- Apps grow in and shrink away as they open and close, the others making room. A started app's icon
  hops and its dot pulses until its window shows. A pinned app dragged off the dock is unpinned.
- Gauges glide to each new reading and their figures roll.
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
