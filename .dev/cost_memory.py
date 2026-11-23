"""The first read of Claude Code's transcripts for the AI card's cost estimate: how long it takes and
the most memory it holds (peak working set), as the running Dockline would do it. Read only."""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))


class Counters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD), ('peak', ctypes.c_size_t),
                ('now', ctypes.c_size_t), ('q1', ctypes.c_size_t), ('q2', ctypes.c_size_t),
                ('q3', ctypes.c_size_t), ('q4', ctypes.c_size_t), ('page', ctypes.c_size_t),
                ('page_peak', ctypes.c_size_t)]


k = ctypes.WinDLL('kernel32')
k.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]


def peak_mb() -> int:
    c = Counters()
    c.cb = ctypes.sizeof(c)
    k.K32GetProcessMemoryInfo(wintypes.HANDLE(-1), ctypes.byref(c), c.cb)
    return c.peak // 2 ** 20


from dockline import ai  # noqa: E402

print('before:', peak_mb(), 'MB peak')
t = time.perf_counter()
estimate = ai.claude_costs().estimate([{'key': 'weekly', 'resets_at': time.time() + 86400}])
print(estimate, f'{time.perf_counter() - t:.1f} s; peak {peak_mb()} MB')
t = time.perf_counter()
ai.claude_costs().estimate([{'key': 'weekly', 'resets_at': time.time() + 86400}])
print(f'again {time.perf_counter() - t:.3f} s')
