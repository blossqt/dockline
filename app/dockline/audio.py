"""Windows' sound outputs: which there are, which one sound goes to, and moving it to another -
and its microphone, muted or not.

Through Core Audio's COM interfaces, called with ctypes. Choosing the default
output goes through IPolicyConfig - the interface Windows' own Sound settings
use, undocumented but unchanged since Windows 7 - for all three of its roles
(games and media, communications, system sounds), as Settings does.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

ole32 = ctypes.WinDLL('ole32')
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.HRESULT
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.PropVariantClear.argtypes = [ctypes.c_void_p]


class GUID(ctypes.Structure):
    _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD),
                ('Data4', ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> 'GUID':
        import uuid
        raw = uuid.UUID(text).bytes_le
        return cls.from_buffer_copy(raw)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', GUID), ('pid', wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [('vt', wintypes.WORD), ('reserved', wintypes.WORD * 3), ('pointer', ctypes.c_void_p),
                ('rest', ctypes.c_void_p)]


CLSID_MMDeviceEnumerator = GUID.of('BCDE0395-E52F-467C-8E3D-C4579291692E')
IID_IMMDeviceEnumerator = GUID.of('A95664D2-9614-4F35-A746-DE8DB63617E6')
CLSID_PolicyConfigClient = GUID.of('870AF99C-171D-4F9E-AF0D-E63DF40C2BC9')
IID_IPolicyConfig = GUID.of('F8679F50-850A-41CF-9C72-430F290290C8')
IID_IAudioEndpointVolume = GUID.of('5CDF2C82-841E-4546-9722-0CF74078229A')
PKEY_Device_FriendlyName = PROPERTYKEY(GUID.of('A45C254E-DF1C-4EFD-8020-67D146A850E0'), 14)

CLSCTX_ALL = 0x17
E_RENDER, E_CAPTURE = 0, 1       # outputs; inputs (microphones)
DEVICE_STATE_ACTIVE = 0x1
ROLES = (0, 1, 2)               # eConsole, eMultimedia, eCommunications
VT_LPWSTR = 31


@dataclass(frozen=True)
class Output:
    id: str
    name: str


class AudioError(OSError):
    pass


def _call(obj: ctypes.c_void_p, index: int, *args, argtypes=(), restype=ctypes.HRESULT):
    """Call method index of a COM object's vtable."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])
    return method(obj, *args)


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        _call(obj, 2, restype=wintypes.ULONG)


def _create(clsid: GUID, iid: GUID) -> ctypes.c_void_p:
    ole32.CoInitializeEx(None, 0x2)          # apartment-threaded; already so on Qt's thread, which is fine
    obj = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(obj))
    return obj


def _device_id(device: ctypes.c_void_p) -> str:
    text = ctypes.c_void_p()
    _call(device, 5, ctypes.byref(text), argtypes=(ctypes.c_void_p,))                  # IMMDevice::GetId
    try:
        return ctypes.wstring_at(text.value)
    finally:
        ole32.CoTaskMemFree(text)


def _device_name(device: ctypes.c_void_p) -> str:
    store = ctypes.c_void_p()
    _call(device, 4, 0, ctypes.byref(store), argtypes=(wintypes.DWORD, ctypes.c_void_p))   # OpenPropertyStore
    try:
        value = PROPVARIANT()
        _call(store, 5, ctypes.byref(PKEY_Device_FriendlyName), ctypes.byref(value),
              argtypes=(ctypes.c_void_p, ctypes.c_void_p))                              # IPropertyStore::GetValue
        try:
            return ctypes.wstring_at(value.pointer) if value.vt == VT_LPWSTR and value.pointer else ''
        finally:
            ole32.PropVariantClear(ctypes.byref(value))
    finally:
        _release(store)


def outputs() -> list[Output]:
    """The sound outputs that are plugged in and turned on, as Windows lists them."""
    try:
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except OSError as error:
        raise AudioError(f'Windows would not list its sound outputs ({error})') from None
    try:
        collection = ctypes.c_void_p()
        _call(enumerator, 3, E_RENDER, DEVICE_STATE_ACTIVE, ctypes.byref(collection),
              argtypes=(ctypes.c_int, wintypes.DWORD, ctypes.c_void_p))                 # EnumAudioEndpoints
        try:
            count = wintypes.UINT()
            _call(collection, 3, ctypes.byref(count), argtypes=(ctypes.c_void_p,))       # GetCount
            found = []
            for index in range(count.value):
                device = ctypes.c_void_p()
                _call(collection, 4, index, ctypes.byref(device), argtypes=(wintypes.UINT, ctypes.c_void_p))
                try:
                    found.append(Output(_device_id(device), _device_name(device)))
                finally:
                    _release(device)
            return found
        finally:
            _release(collection)
    finally:
        _release(enumerator)


def current() -> str:
    """The id of the output sound goes to now, or '' if there is none."""
    try:
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except OSError:
        return ''
    try:
        device = ctypes.c_void_p()
        try:
            _call(enumerator, 4, E_RENDER, 1, ctypes.byref(device),
                  argtypes=(ctypes.c_int, ctypes.c_int, ctypes.c_void_p))               # GetDefaultAudioEndpoint
        except OSError:
            return ''                                       # none plugged in
        try:
            return _device_id(device)
        finally:
            _release(device)
    finally:
        _release(enumerator)


def choose(output_id: str) -> None:
    """Send the sound to this output, for everything, as Windows' Sound settings would."""
    try:
        policy = _create(CLSID_PolicyConfigClient, IID_IPolicyConfig)
    except OSError as error:
        raise AudioError(f'Windows would not change its sound output ({error})') from None
    try:
        for role in ROLES:
            _call(policy, 13, output_id, role, argtypes=(wintypes.LPCWSTR, ctypes.c_int))   # SetDefaultEndpoint
    finally:
        _release(policy)


def next_output(chosen: list[str]) -> Output | None:
    """The output after the one sound goes to now, among those chosen to switch between (all of
    them, with fewer than two chosen and plugged in) - or None with nothing to switch to."""
    present = outputs()
    ring = [o for o in present if o.id in chosen]
    if len(ring) < 2:
        ring = present
    if len(ring) < 2:
        return None
    now = current()
    ids = [o.id for o in ring]
    return ring[(ids.index(now) + 1) % len(ring)] if now in ids else ring[0]


# --------------------------------------------------------------------------- the volume

def _output_volume(enumerator: ctypes.c_void_p) -> ctypes.c_void_p | None:
    """The default output's IAudioEndpointVolume, to be released - None with no output."""
    device = ctypes.c_void_p()
    try:
        _call(enumerator, 4, E_RENDER, 1, ctypes.byref(device),
              argtypes=(ctypes.c_int, ctypes.c_int, ctypes.c_void_p))                   # GetDefaultAudioEndpoint
    except OSError:
        return None
    try:
        return _volume(device)
    finally:
        _release(device)


def volume() -> int | None:
    """How loud the output sound goes to is, 0 to 100 as Windows shows it - None with no output."""
    try:
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except OSError:
        return None
    try:
        endpoint = _output_volume(enumerator)
        if endpoint is None:
            return None
        try:
            level = ctypes.c_float()
            _call(endpoint, 9, ctypes.byref(level), argtypes=(ctypes.c_void_p,))    # GetMasterVolumeLevelScalar
            return round(level.value * 100)
        finally:
            _release(endpoint)
    finally:
        _release(enumerator)


def set_volume(percent: int) -> None:
    """Set the output's volume, 0 to 100 - unmuting it, as Windows' own slider does."""
    try:
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except OSError as error:
        raise AudioError(f'Windows would not find its sound output ({error})') from None
    try:
        endpoint = _output_volume(enumerator)
        if endpoint is None:
            return
        try:
            _call(endpoint, 7, max(0.0, min(1.0, percent / 100)), None,
                  argtypes=(ctypes.c_float, ctypes.c_void_p))                           # SetMasterVolumeLevelScalar
            muted = wintypes.BOOL()
            _call(endpoint, 15, ctypes.byref(muted), argtypes=(ctypes.c_void_p,))      # GetMute
            if muted.value and percent > 0:
                _call(endpoint, 14, False, None, argtypes=(wintypes.BOOL, ctypes.c_void_p))   # SetMute
        finally:
            _release(endpoint)
    finally:
        _release(enumerator)


# --------------------------------------------------------------------------- the microphone

def _microphones(enumerator: ctypes.c_void_p) -> list[ctypes.c_void_p]:
    """Windows' default microphone, and its default one for calls (as Discord uses) if that is
    another - each an IMMDevice, to be released."""
    found, ids = [], set()
    for role in (0, 2):                                     # eConsole, eCommunications
        device = ctypes.c_void_p()
        try:
            _call(enumerator, 4, E_CAPTURE, role, ctypes.byref(device),
                  argtypes=(ctypes.c_int, ctypes.c_int, ctypes.c_void_p))               # GetDefaultAudioEndpoint
        except OSError:
            continue                                        # none plugged in
        device_id = _device_id(device)
        if device_id in ids:
            _release(device)
        else:
            ids.add(device_id)
            found.append(device)
    return found


def _volume(device: ctypes.c_void_p) -> ctypes.c_void_p:
    volume = ctypes.c_void_p()
    _call(device, 3, ctypes.byref(IID_IAudioEndpointVolume), CLSCTX_ALL, None, ctypes.byref(volume),
          argtypes=(ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p))    # IMMDevice::Activate
    return volume


def mic_muted() -> bool | None:
    """Whether Windows' default microphone is muted - None with no microphone plugged in."""
    try:
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except OSError:
        return None
    try:
        devices = _microphones(enumerator)
        if not devices:
            return None
        try:
            volume = _volume(devices[0])
            try:
                muted = wintypes.BOOL()
                _call(volume, 15, ctypes.byref(muted), argtypes=(ctypes.c_void_p,))    # IAudioEndpointVolume::GetMute
                return bool(muted.value)
            finally:
                _release(volume)
        finally:
            for device in devices:
                _release(device)
    finally:
        _release(enumerator)


def mute_mic(muted: bool) -> None:
    """Mute the microphone, or not: Windows' default one, and its default one for calls if that is
    another - so nothing hears it, whichever it records from."""
    try:
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except OSError as error:
        raise AudioError(f'Windows would not find its microphone ({error})') from None
    try:
        devices = _microphones(enumerator)
        try:
            for device in devices:
                volume = _volume(device)
                try:
                    _call(volume, 14, bool(muted), None, argtypes=(wintypes.BOOL, ctypes.c_void_p))   # SetMute
                finally:
                    _release(volume)
        finally:
            for device in devices:
                _release(device)
    finally:
        _release(enumerator)


# --------------------------------------------------------------------------- the output's mute

def output_muted() -> bool | None:
    """Whether the output sound goes to is muted - None with no output."""
    try:
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except OSError:
        return None
    try:
        endpoint = _output_volume(enumerator)
        if endpoint is None:
            return None
        try:
            muted = wintypes.BOOL()
            _call(endpoint, 15, ctypes.byref(muted), argtypes=(ctypes.c_void_p,))      # GetMute
            return bool(muted.value)
        finally:
            _release(endpoint)
    finally:
        _release(enumerator)


def mute_output(muted: bool) -> None:
    try:
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except OSError:
        return
    try:
        endpoint = _output_volume(enumerator)
        if endpoint is None:
            return
        try:
            _call(endpoint, 14, bool(muted), None, argtypes=(wintypes.BOOL, ctypes.c_void_p))   # SetMute
        finally:
            _release(endpoint)
    finally:
        _release(enumerator)


# --------------------------------------------------------------------------- each app's volume

IID_IAudioSessionManager2 = GUID.of('77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F')
IID_IAudioSessionControl2 = GUID.of('BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D')
IID_ISimpleAudioVolume = GUID.of('87CE5498-68D6-44E5-9215-6DA47EF883D8')
SESSION_EXPIRED = 2


@dataclass(frozen=True)
class AppVolume:
    """An app's sound on the output, as Windows' Volume mixer lists it: one row per program,
    however many sessions it has (a browser has several)."""
    key: str                     # the program's path, lower case ('' for system sounds)
    pid: int                     # one of its processes
    name: str                    # its name as the session gives it, or '' (then the program's)
    volume: int                  # 0-100
    muted: bool
    system: bool = False


def _query(obj: ctypes.c_void_p, iid: GUID) -> ctypes.c_void_p | None:
    found = ctypes.c_void_p()
    try:
        _call(obj, 0, ctypes.byref(iid), ctypes.byref(found), argtypes=(ctypes.c_void_p, ctypes.c_void_p))
    except OSError:
        return None
    return found if found else None


def _sessions(each) -> None:
    """Call each(control2, simple_volume) for every live session on the default output."""
    enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    try:
        device = ctypes.c_void_p()
        try:
            _call(enumerator, 4, E_RENDER, 1, ctypes.byref(device),
                  argtypes=(ctypes.c_int, ctypes.c_int, ctypes.c_void_p))                # GetDefaultAudioEndpoint
        except OSError:
            return
        try:
            manager = ctypes.c_void_p()
            _call(device, 3, ctypes.byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, ctypes.byref(manager),
                  argtypes=(ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p))   # Activate
            try:
                listing = ctypes.c_void_p()
                _call(manager, 5, ctypes.byref(listing), argtypes=(ctypes.c_void_p,))   # GetSessionEnumerator
                try:
                    count = ctypes.c_int()
                    _call(listing, 3, ctypes.byref(count), argtypes=(ctypes.c_void_p,))  # GetCount
                    for index in range(count.value):
                        control = ctypes.c_void_p()
                        _call(listing, 4, index, ctypes.byref(control),
                              argtypes=(ctypes.c_int, ctypes.c_void_p))                   # GetSession
                        try:
                            state = ctypes.c_int()
                            _call(control, 3, ctypes.byref(state), argtypes=(ctypes.c_void_p,))   # GetState
                            if state.value == SESSION_EXPIRED:
                                continue
                            second = _query(control, IID_IAudioSessionControl2)
                            simple = _query(control, IID_ISimpleAudioVolume)
                            try:
                                if second is not None and simple is not None:
                                    each(second, simple)
                            finally:
                                _release(second)
                                _release(simple)
                        finally:
                            _release(control)
                finally:
                    _release(listing)
            finally:
                _release(manager)
        finally:
            _release(device)
    finally:
        _release(enumerator)


def _session_facts(second: ctypes.c_void_p, simple: ctypes.c_void_p) -> tuple[int, str, bool, float, bool]:
    pid = wintypes.DWORD()
    _call(second, 14, ctypes.byref(pid), argtypes=(ctypes.c_void_p,))                       # GetProcessId
    system = _call(second, 15, restype=ctypes.c_long) == 0                                  # IsSystemSoundsSession: S_OK
    text = ctypes.c_void_p()
    name = ''
    try:
        _call(second, 4, ctypes.byref(text), argtypes=(ctypes.c_void_p,))                    # GetDisplayName
        if text.value:
            name = ctypes.wstring_at(text.value)
            ole32.CoTaskMemFree(text)
    except OSError:
        pass
    level = ctypes.c_float()
    _call(simple, 4, ctypes.byref(level), argtypes=(ctypes.c_void_p,))                      # GetMasterVolume
    muted = wintypes.BOOL()
    _call(simple, 6, ctypes.byref(muted), argtypes=(ctypes.c_void_p,))                      # GetMute
    return pid.value, name, system, level.value, bool(muted.value)


def app_volumes(path_of=None) -> list[AppVolume]:
    """The apps with sound on the default output (system sounds first), one row per program."""
    import os
    if path_of is None:
        from .win import process_path as path_of
    rows: dict[str, AppVolume] = {}

    def each(second, simple):
        pid, name, system, level, muted = _session_facts(second, simple)
        if pid == os.getpid():
            return
        key = '' if system else (path_of(pid) or f'pid:{pid}').lower()
        if key in rows:
            return                                      # the program's first session speaks for it
        rows[key] = AppVolume(key, pid, '' if name.startswith('@') else name, round(level * 100), muted, system)

    try:
        _sessions(each)
    except OSError:
        return []
    return sorted(rows.values(), key=lambda row: (not row.system, (row.name or row.key).lower()))


def set_app_volume(key: str, percent: int | None = None, muted: bool | None = None, path_of=None) -> None:
    """Set one program's volume (0-100) and/or mute, on every session it has, as the mixer does."""
    import os
    if path_of is None:
        from .win import process_path as path_of

    def each(second, simple):
        pid, _name, system, _level, _muted = _session_facts(second, simple)
        if pid == os.getpid():
            return
        if ('' if system else (path_of(pid) or f'pid:{pid}').lower()) != key:
            return
        if percent is not None:
            _call(simple, 3, max(0.0, min(1.0, percent / 100)), None,
                  argtypes=(ctypes.c_float, ctypes.c_void_p))                                # SetMasterVolume
        if muted is not None:
            _call(simple, 5, bool(muted), None, argtypes=(wintypes.BOOL, ctypes.c_void_p))   # SetMute

    _sessions(each)
