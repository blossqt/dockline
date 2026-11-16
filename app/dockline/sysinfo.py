"""What the PC is doing: processor, memory, network, battery, storage and the recycle bin."""

from __future__ import annotations

import ctypes
import shutil
import time
from ctypes import wintypes

kernel32 = ctypes.WinDLL('kernel32')
shell32 = ctypes.WinDLL('shell32')
iphlpapi = ctypes.WinDLL('iphlpapi')


class _FileTime(ctypes.Structure):
    _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]

    def value(self) -> int:
        return (self.high << 32) | self.low


class Processor:
    """Processor load as a share, from the change in Windows' idle and busy times."""

    def __init__(self):
        self.last = None

    def read(self) -> float:
        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        now = idle.value(), kernel.value(), user.value()
        load = 0.0
        if self.last:
            d_idle, d_kernel, d_user = (a - b for a, b in zip(now, self.last))
            total = d_kernel + d_user                # kernel time includes idle time
            if total > 0:
                load = max(0.0, min(1.0, (total - d_idle) / total))
        self.last = now
        return load


class _MemoryStatus(ctypes.Structure):
    _fields_ = [('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD),
                ('ullTotalPhys', ctypes.c_uint64), ('ullAvailPhys', ctypes.c_uint64),
                ('ullTotalPageFile', ctypes.c_uint64), ('ullAvailPageFile', ctypes.c_uint64),
                ('ullTotalVirtual', ctypes.c_uint64), ('ullAvailVirtual', ctypes.c_uint64),
                ('ullAvailExtendedVirtual', ctypes.c_uint64)]


def memory() -> tuple[float, float, float]:
    """Share in use, gigabytes in use, gigabytes in all."""
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    total = status.ullTotalPhys / 2 ** 30
    used = (status.ullTotalPhys - status.ullAvailPhys) / 2 ** 30
    return (used / total if total else 0.0), used, total


class _Guid(ctypes.Structure):
    _fields_ = [('a', ctypes.c_ulong), ('b', ctypes.c_ushort), ('c', ctypes.c_ushort), ('d', ctypes.c_ubyte * 8)]


_U64 = ctypes.c_uint64


class _IfRow2(ctypes.Structure):
    _fields_ = [('Luid', _U64), ('Index', ctypes.c_ulong), ('Guid', _Guid), ('Alias', ctypes.c_wchar * 257),
                ('Description', ctypes.c_wchar * 257), ('PhysicalLength', ctypes.c_ulong),
                ('Physical', ctypes.c_ubyte * 32), ('Permanent', ctypes.c_ubyte * 32), ('Mtu', ctypes.c_ulong),
                ('Type', ctypes.c_ulong), ('Tunnel', ctypes.c_int), ('Media', ctypes.c_int),
                ('Medium', ctypes.c_int), ('Access', ctypes.c_int), ('Direction', ctypes.c_int),
                ('Flags', ctypes.c_ubyte), ('Oper', ctypes.c_int), ('Admin', ctypes.c_int),
                ('Connected', ctypes.c_int), ('Network', _Guid), ('Connection', ctypes.c_int),
                ('TransmitSpeed', _U64), ('ReceiveSpeed', _U64), ('InOctets', _U64)] + \
               [(name, _U64) for name in ('InU', 'InNU', 'InDiscards', 'InErrors', 'InUnknown', 'InUO', 'InMO',
                                          'InBO')] + \
               [('OutOctets', _U64)] + \
               [(name, _U64) for name in ('OutU', 'OutNU', 'OutDiscards', 'OutErrors', 'OutUO', 'OutMO', 'OutBO',
                                          'OutQ')]


assert ctypes.sizeof(_IfRow2) == 1352


def _octets() -> tuple[int, int]:
    """Bytes received and sent by the PC's own network adapters, since they started."""
    table = ctypes.c_void_p()
    if iphlpapi.GetIfTable2(ctypes.byref(table)) != 0:
        return 0, 0
    try:
        count = ctypes.c_ulong.from_address(table.value).value
        rows = (_IfRow2 * count).from_address(table.value + 8)
        down = up = 0
        for row in rows:
            if row.Flags & 1 and not row.Flags & 2 and row.Oper == 1:    # hardware, not a filter, up
                down += row.InOctets
                up += row.OutOctets
        return down, up
    finally:
        iphlpapi.FreeMibTable(table)


class Network:
    """Download and upload speed in bytes a second."""

    def __init__(self):
        self.last = None

    def read(self) -> tuple[float, float]:
        now = time.perf_counter(), *_octets()
        speeds = 0.0, 0.0
        if self.last:
            seconds = now[0] - self.last[0]
            if seconds > 0:
                speeds = (max(0, now[1] - self.last[1]) / seconds, max(0, now[2] - self.last[2]) / seconds)
        self.last = now
        return speeds


def rate(value: float) -> str:
    for unit, size in (('GB/s', 2 ** 30), ('MB/s', 2 ** 20), ('KB/s', 2 ** 10)):
        if value >= size:
            amount = value / size
            return f'{amount:.1f} {unit}' if amount < 100 else f'{amount:.0f} {unit}'
    return f'{value:.0f} B/s'


class _PowerStatus(ctypes.Structure):
    _fields_ = [('ACLineStatus', ctypes.c_ubyte), ('BatteryFlag', ctypes.c_ubyte),
                ('BatteryLifePercent', ctypes.c_ubyte), ('SystemStatusFlag', ctypes.c_ubyte),
                ('BatteryLifeTime', wintypes.DWORD), ('BatteryFullLifeTime', wintypes.DWORD)]


def battery() -> tuple[int, bool] | None:
    """Charge in percent and whether it is plugged in - None without a battery."""
    status = _PowerStatus()
    if not kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    if status.BatteryFlag & 128 or status.BatteryFlag == 255 or status.BatteryLifePercent > 100:
        return None
    return status.BatteryLifePercent, status.ACLineStatus == 1


def storage(drive: str = 'C:\\') -> tuple[float, float]:
    """Gigabytes free and in all."""
    try:
        usage = shutil.disk_usage(drive)
    except OSError:
        return 0.0, 0.0
    return usage.free / 2 ** 30, usage.total / 2 ** 30


class _BinInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('i64Size', ctypes.c_int64), ('i64NumItems', ctypes.c_int64)]


def recycle_bin() -> tuple[int, int]:
    """Items and bytes in the recycle bin."""
    info = _BinInfo()
    info.cbSize = ctypes.sizeof(info)
    if shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0, 0
    return info.i64NumItems, info.i64Size


def empty_recycle_bin(hwnd: int) -> None:
    """Windows asks for confirmation itself."""
    shell32.SHEmptyRecycleBinW(wintypes.HWND(hwnd), None, 0)


def size_text(value: float) -> str:
    for unit, size in (('TB', 2 ** 40), ('GB', 2 ** 30), ('MB', 2 ** 20), ('KB', 2 ** 10)):
        if value >= size:
            return f'{value / size:.1f} {unit}'
    return f'{value:.0f} B'
