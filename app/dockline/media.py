"""What is playing, app by app: Windows' media sessions (the ones its own media flyout shows),
read and controlled on a thread of their own so the dock never waits on them."""

from __future__ import annotations

import asyncio
import ctypes
import os
import threading
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _Status)
    from winrt.windows.storage.streams import DataReader as _Reader
    AVAILABLE = True
except Exception:                                    # pragma: no cover - Windows without the media API
    AVAILABLE = False


@dataclass(frozen=True)
class Session:
    id: str                      # the app's id as Windows names it (an AppUserModelID, or 'Spotify.exe')
    app: str                     # the app's name
    title: str
    artist: str
    playing: bool
    can_toggle: bool = True
    can_previous: bool = False
    can_next: bool = False
    art: bytes = b''             # the album art or video thumbnail, as an image file's bytes
    position: float = 0.0        # seconds into it, when last told (at stamp)
    duration: float = 0.0        # its length in seconds; 0 when the app gives none
    stamp: float = 0.0           # when position was right (time.time())
    can_seek: bool = False

    def now(self) -> float:
        """Where it is now: while it plays, on from where it was last told."""
        import time
        moved = max(0.0, time.time() - self.stamp) if self.playing and self.stamp else 0.0
        return max(0.0, min(self.duration or 1e9, self.position + moved))


# ---------------------------------------------------------------- app names

_shell32 = ctypes.WinDLL('shell32')
_ole32 = ctypes.WinDLL('ole32')
_shell32.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                        wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
_shell32.SHGetNameFromIDList.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(wintypes.LPWSTR)]
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_names: dict[str, str] = {}


def app_name(app_id: str) -> str:
    """An app's name as the Start menu shows it, from its id; a plain program's from its file name."""
    if app_id in _names:
        return _names[app_id]
    name = ''
    pidl = ctypes.c_void_p()
    if not app_id.lower().endswith('.exe') and \
            _shell32.SHParseDisplayName(f'shell:AppsFolder\\{app_id}', None, ctypes.byref(pidl), 0, None) == 0:
        text = wintypes.LPWSTR()
        if _shell32.SHGetNameFromIDList(pidl, 0, ctypes.byref(text)) == 0:     # SIGDN_NORMALDISPLAY
            name = text.value or ''
            _ole32.CoTaskMemFree(text)
        _ole32.CoTaskMemFree(pidl)
    if not name:
        base = app_id.split('!')[-1].split('\\')[-1]
        base = os.path.splitext(base)[0] if base.lower().endswith('.exe') else base
        name = base.split('.')[-1] if '_' not in base else base.split('_')[0].split('.')[-1]
        name = {'msedge': 'Microsoft Edge', 'chrome': 'Google Chrome', 'firefox': 'Firefox',
                'spotify': 'Spotify', 'zunemusic': 'Media Player'}.get(name.lower(), name[:1].upper() + name[1:])
    _names[app_id] = name
    return name


# ---------------------------------------------------------------- the sessions

class Media(QObject):
    """Windows' media sessions. changed(list[Session]) comes on the window's thread (a queued signal)."""

    changed = Signal(list)

    def __init__(self):
        super().__init__()
        self.sessions: list[Session] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.manager = None
        self.every = 3.0                       # seconds between looks; quicker while the card is open
        self._art: dict[tuple[str, str], bytes] = {}
        self._wake: asyncio.Event | None = None

    def start(self) -> None:
        if not AVAILABLE or self.loop is not None:
            return
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, name='media', daemon=True).start()

    def quick(self, on: bool) -> None:
        """Look every half second (the card is open) or every three."""
        self.every = 0.5 if on else 3.0
        self.refresh_soon()

    def refresh_soon(self) -> None:
        if self.loop is not None and self._wake is not None:
            self.loop.call_soon_threadsafe(self._wake.set)

    def command(self, session_id: str, what: str) -> None:
        """'toggle', 'next' or 'previous' for one app's session."""
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self._command(session_id, what), self.loop)

    # -- on the media thread
    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._watch())

    async def _watch(self) -> None:
        self._wake = asyncio.Event()
        try:
            self.manager = await _Manager.request_async()
        except Exception:
            return
        try:
            self.manager.add_sessions_changed(lambda *_: self.loop.call_soon_threadsafe(self._wake.set))
        except Exception:
            pass
        while True:
            try:
                found = await self._look()
            except Exception:
                found = self.sessions
            if found != self.sessions:
                self.sessions = found
                self.changed.emit(list(found))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self.every)
            except asyncio.TimeoutError:
                pass

    async def _look(self) -> list[Session]:
        found = []
        for session in list(self.manager.get_sessions()):
            app_id = session.source_app_user_model_id or ''
            try:
                props = await session.try_get_media_properties_async()
                title, artist = props.title or '', props.artist or props.album_title or ''
            except Exception:
                props, title, artist = None, '', ''
            info = session.get_playback_info()
            controls = info.controls
            art = self._art.get((app_id, title))
            if art is None:
                art = await self._read_art(props)
                if len(self._art) > 32:
                    self._art.clear()
                self._art[(app_id, title)] = art
            position, duration, stamp = _timeline(session)
            found.append(Session(app_id, app_name(app_id), title, artist,
                                 info.playback_status == _Status.PLAYING,
                                 bool(controls.is_play_pause_toggle_enabled or controls.is_play_enabled
                                      or controls.is_pause_enabled),
                                 bool(controls.is_previous_enabled), bool(controls.is_next_enabled), art,
                                 position, duration, stamp,
                                 bool(controls.is_playback_position_enabled) and duration > 0))
        found.sort(key=lambda s: not s.playing)              # what plays first, as Android's player
        return found

    @staticmethod
    async def _read_art(props) -> bytes:
        if props is None or props.thumbnail is None:
            return b''
        try:
            stream = await props.thumbnail.open_read_async()
            size = int(stream.size)
            if not 0 < size < 8_000_000:
                return b''
            reader = _Reader(stream.get_input_stream_at(0))
            await reader.load_async(size)
            return bytes(reader.read_buffer(size))
        except Exception:
            return b''

    def seek(self, session_id: str, seconds: float) -> None:
        """Jump to a point in one app's track or video."""
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self._command(session_id, 'seek', seconds), self.loop)

    async def _command(self, session_id: str, what: str, seconds: float = 0.0) -> None:
        if self.manager is None:
            return
        for session in list(self.manager.get_sessions()):
            if (session.source_app_user_model_id or '') == session_id:
                try:
                    if what == 'seek':
                        start = session.get_timeline_properties().start_time.total_seconds()
                        await session.try_change_playback_position_async(round((start + seconds) * 10_000_000))
                    elif what == 'toggle':
                        await session.try_toggle_play_pause_async()
                    elif what == 'next':
                        await session.try_skip_next_async()
                    elif what == 'previous':
                        await session.try_skip_previous_async()
                except Exception:
                    pass
                break
        await asyncio.sleep(0.15)
        self._wake.set()


def _timeline(session) -> tuple[float, float, float]:
    """(seconds in, length in seconds, when that was so) from a session's timeline."""
    import time
    try:
        line = session.get_timeline_properties()
        start = line.start_time.total_seconds()
        duration = max(0.0, line.end_time.total_seconds() - start)
        position = max(0.0, line.position.total_seconds() - start)
        try:
            stamp = line.last_updated_time.timestamp()
        except (OverflowError, OSError, ValueError, AttributeError):
            stamp = 0.0
        if not stamp or stamp < 946684800 or stamp > time.time() + 5:      # unset (1601) or nonsense
            stamp = time.time()
        return position, duration, stamp
    except Exception:
        return 0.0, 0.0, 0.0


_media: Media | None = None


def media() -> Media:
    """The one Media, started on first use."""
    global _media
    if _media is None:
        _media = Media()
        _media.start()
    return _media
