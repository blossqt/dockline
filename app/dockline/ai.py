"""AI plan allowances (Claude, ChatGPT, Gemini), read locally and from each vendor's own usage endpoint.

Reads the sign-ins already kept on this PC by Claude Code (~/.claude/.credentials.json), OpenAI Codex
(~/.codex/auth.json and ~/.codex/sessions) and Gemini CLI / Antigravity CLI (~/.gemini/oauth_creds.json
and Windows Credential Manager). Read-only: credential stores are never written by Dockline, and tokens
are never saved in Dockline's settings or cache. An expired sign-in is renewed by the vendor's own CLI
(`claude auth status`, `agy models`), which writes its own store; neither spends any allowance.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import gzip
import json
import math
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from .config import folder

PROVIDERS = ('claude', 'chatgpt', 'gemini')

INFO = {
    'claude': {
        'name': 'Claude',
        'short': 'Claude',
        'title': 'Claude Usage',
        'hint': 'Sign in with Claude Code (claude login) on this PC to show your live allowance.',
    },
    'chatgpt': {
        'name': 'ChatGPT',
        'short': 'ChatGPT',
        'title': 'ChatGPT Usage',
        'hint': 'Sign in with OpenAI Codex (codex login) on this PC to show your 5-hour and weekly windows.',
    },
    'gemini': {
        'name': 'Gemini',
        'short': 'Gemini',
        'title': 'Gemini Usage',
        'hint': 'Sign in with Gemini CLI or Antigravity on this PC to show your session and weekly quotas.',
    },
}

CLAUDE_USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
CHATGPT_USAGE_URL = 'https://chatgpt.com/backend-api/wham/usage'
GEMINI_LOAD_URL = 'https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist'
GEMINI_QUOTA_URL = 'https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota'
GEMINI_SUMMARY_URL = 'https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary'
ANTIGRAVITY_LOAD_URL = 'https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist'
ANTIGRAVITY_QUOTA_URL = 'https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota'
ANTIGRAVITY_SUMMARY_URL = 'https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary'
ANTIGRAVITY_UA = 'antigravity/hub/2.24.0'

REFRESH_MODES = ('live', '30s', '1m', '5m')
REFRESH_SECONDS = {
    'live': 45,         # background network sync in Live mode (plus local session watching)
    '30s': 30,          # 30-second background polling
    '1m': 60,           # 1-minute background polling
    '5m': 300,          # 5-minute background polling
}

POLL_EVERY = 45         # default seconds between background network refreshes
CARD_POLL_EVERY = 20    # seconds between refreshes while an AI popup card is open
WATCH_EVERY = 2         # seconds between local credential/session activity checks
LIVE_DEBOUNCE = 1.5     # quiet seconds after local file activity before polling
LIVE_MAX_WAIT = 4.0     # max seconds to wait during continuous streaming before polling
MIN_LIVE_GAP = 12.0     # minimum seconds between automatic live polls per provider
RATE_LIMIT_WAIT = 120   # default seconds to back off if a provider answers HTTP 429
RATE_LIMIT_SAY = 900    # s: a reading kept this long through rate limits says why it is not newer
RATE_LIMITED = 'Usage requests are rate-limited. Retrying automatically.'
LOW_THRESHOLD = 15.0    # warn once per reset window when remaining allowance drops to or below 15%
CREATE_NO_WINDOW = 0x08000000


def normalize_refresh_mode(value) -> str:
    return value if value in REFRESH_SECONDS else 'live'


def poll_interval_for(mode: str | None, card_open: bool = False) -> int:
    norm = normalize_refresh_mode(mode)
    base = REFRESH_SECONDS[norm]
    if card_open:
        return min(base, CARD_POLL_EVERY if norm != '5m' else 30)
    return base


GEMINI_CRED_TARGETS = (
    'gemini-cli-oauth',
    'gemini-cli-oauth:main-account',
    'gemini-cli-oauth/main-account',
    'gemini:antigravity',
)


class _HttpStatus(Exception):
    def __init__(self, code: int, detail: str = ''):
        super().__init__(f'HTTP {code}' + (f': {detail}' if detail else ''))
        self.code = code


class _RateLimited(Exception):
    def __init__(self, retry_after: float | None = None):
        super().__init__('HTTP 429')
        self.retry_after = retry_after


# ---------------------------------------------------------------- time & percentages

def parse_time(value) -> float | None:
    """Turn an epoch timestamp (seconds or milliseconds) or ISO-8601 string into epoch seconds."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        val = float(value)
        if val <= 0:
            return None
        return val / 1000.0 if val > 1e11 else val
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            num = float(text)
            return parse_time(num)
        except ValueError:
            pass
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            parsed = dt.datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return None
    return None


def effective_used(window: dict, now: float | None = None) -> float:
    """Percent used (0..100). Once a window's reset time has passed, its used share resets to 0."""
    current = time.time() if now is None else now
    resets_at = window.get('resets_at')
    if resets_at is not None and resets_at <= current:
        return 0.0
    try:
        return max(0.0, min(100.0, float(window.get('used', 0.0))))
    except (TypeError, ValueError):
        return 0.0


def remaining_pct(window: dict, now: float | None = None) -> float:
    return max(0.0, min(100.0, 100.0 - effective_used(window, now)))


def shown_value(window: dict, show_used: bool = False, now: float | None = None) -> tuple[int, float, float]:
    """Return (rounded_percent, bar_share_0_to_1, left_percent) for either Remaining or Used mode."""
    used = effective_used(window, now)
    left = max(0.0, 100.0 - used)
    shown = used if show_used else left
    return round(shown), max(0.0, min(1.0, shown / 100.0)), left


def format_reset(resets_at: float | None, now: float | None = None) -> str:
    """Compact reset label for dock tiles: '42m', '2h 14m', or 'Thu 19' for multi-day windows."""
    if resets_at is None:
        return ''
    current = time.time() if now is None else now
    delta = resets_at - current
    if delta <= 0:
        return 'now'
    if delta < 3600:
        return f'{max(1, round(delta / 60))}m'
    if delta < 86400:
        total_mins = int((delta + 30) // 60)
        hours, mins = divmod(total_mins, 60)
        return f'{hours}h {mins}m' if mins else f'{hours}h'
    when = dt.datetime.fromtimestamp(resets_at)
    return f'{when:%a} {when.day}'


def format_reset_long(resets_at: float | None, clock24: bool = False, now: float | None = None,
                      idle: bool = False) -> str:
    """Detailed reset text for popup cards."""
    if resets_at is None:
        return 'Starts on next message' if idle else 'Reset time unavailable'
    current = time.time() if now is None else now
    delta = resets_at - current
    if delta <= 0:
        return 'Reset window passed'
    when = dt.datetime.fromtimestamp(resets_at)
    clock = when.strftime('%H:%M') if clock24 else when.strftime('%I:%M %p').lstrip('0')
    if delta < 86400:
        return f'Resets in {format_reset(resets_at, current)} · {clock}'
    return f'Resets {when:%a, %b} {when.day} · {clock}'


def format_reset_card(resets_at: float | None, now: float | None = None, idle: bool = False) -> str:
    """Compact reset text beneath circular progress gauges in popup cards."""
    if resets_at is None:
        return 'Next msg' if idle else ''
    current = time.time() if now is None else now
    delta = resets_at - current
    if delta <= 0:
        return 'Now'
    if delta < 86400:
        return format_reset(resets_at, current)
    when = dt.datetime.fromtimestamp(resets_at)
    return f'{when:%a, %b} {when.day}'


def format_age(updated_at: float | None, now: float | None = None) -> str:
    if updated_at is None:
        return ''
    current = time.time() if now is None else now
    age = max(0.0, current - updated_at)
    if age < 10:
        return 'Updated just now'
    if age < 60:
        return f'Updated {int(age // 5) * 5}s ago'
    if age < 3600:
        return f'Updated {max(1, round(age / 60))}m ago'
    return f'Updated {max(1, round(age / 3600))}h ago'


# ---------------------------------------------------------------- credential & log paths

def claude_credentials_path(home: Path | None = None) -> Path:
    if home is None and os.environ.get('CLAUDE_CONFIG_DIR'):
        return Path(os.environ['CLAUDE_CONFIG_DIR']) / '.credentials.json'
    return (home or Path.home()) / '.claude' / '.credentials.json'


def codex_home_path(home: Path | None = None) -> Path:
    if home is None and os.environ.get('CODEX_HOME'):
        return Path(os.environ['CODEX_HOME'])
    return (home or Path.home()) / '.codex'


def gemini_credentials_path(home: Path | None = None) -> Path:
    if home is None and os.environ.get('GEMINI_CLI_HOME'):
        return Path(os.environ['GEMINI_CLI_HOME']) / '.gemini' / 'oauth_creds.json'
    return (home or Path.home()) / '.gemini' / 'oauth_creds.json'


# ---------------------------------------------------------------- Windows Credential Manager (Gemini CLI)

class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ('Flags', wintypes.DWORD),
        ('Type', wintypes.DWORD),
        ('TargetName', wintypes.LPWSTR),
        ('Comment', wintypes.LPWSTR),
        ('LastWritten', wintypes.FILETIME),
        ('CredentialBlobSize', wintypes.DWORD),
        ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
        ('Persist', wintypes.DWORD),
        ('AttributeCount', wintypes.DWORD),
        ('Attributes', ctypes.c_void_p),
        ('TargetAlias', wintypes.LPWSTR),
        ('UserName', wintypes.LPWSTR),
    ]


def _extract_gemini_token(payload) -> tuple[str, float | None]:
    """Pull (access_token, expiry_epoch) from any Gemini CLI or Antigravity CLI credential JSON shape."""
    if not isinstance(payload, dict):
        return '', None
    for container in (payload, payload.get('token'), payload.get('credentials'), payload.get('oauth')):
        if not isinstance(container, dict):
            continue
        tok = str(container.get('access_token') or container.get('accessToken') or '').strip()
        if tok:
            exp = parse_time(container.get('expiry_date') or container.get('expiry') or container.get('expires_at'))
            return tok, exp
    return '', None


def _decode_cred_blob(raw: bytes) -> dict | None:
    if not raw:
        return None
    for enc in ('utf-8', 'utf-16-le'):
        try:
            text = raw.decode(enc).strip('\x00').strip()
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (UnicodeDecodeError, ValueError):
            continue
    return None


def _filetime_seconds(ft: wintypes.FILETIME) -> float:
    ticks = (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)
    return max(0.0, ticks / 10_000_000.0 - 11_644_473_600.0) if ticks else 0.0


def read_windows_gemini_credentials() -> list[tuple[str, str, float | None, float]]:
    """Read Gemini CLI / Antigravity OAuth tokens from Windows Credential Manager (read-only).

    Returns a list of (target_name, access_token, expiry_epoch, written_mtime).
    """
    if os.name != 'nt':
        return []
    advapi32 = ctypes.windll.advapi32
    advapi32.CredReadW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredEnumerateW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                        ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)))]
    advapi32.CredEnumerateW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]

    found: list[tuple[str, str, float | None, float]] = []
    seen_targets: set[str] = set()

    def inspect(cred: _CREDENTIALW) -> None:
        target = str(cred.TargetName or '')
        if not target or target in seen_targets or cred.CredentialBlobSize <= 0:
            return
        seen_targets.add(target)
        raw = bytes(cred.CredentialBlob[:cred.CredentialBlobSize])
        parsed = _decode_cred_blob(raw)
        if parsed is None:
            return
        token, expiry = _extract_gemini_token(parsed)
        if token:
            found.append((target, token, expiry, _filetime_seconds(cred.LastWritten)))

    for name in GEMINI_CRED_TARGETS:
        ptr = ctypes.POINTER(_CREDENTIALW)()
        if advapi32.CredReadW(name, 1, 0, ctypes.byref(ptr)):
            try:
                inspect(ptr.contents)
            finally:
                advapi32.CredFree(ptr)

    for pattern in ('gemini*', '*gemini-cli*'):
        count = wintypes.DWORD(0)
        arr = ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))()
        if advapi32.CredEnumerateW(pattern, 0, ctypes.byref(count), ctypes.byref(arr)):
            try:
                for i in range(count.value):
                    inspect(arr[i].contents)
            finally:
                advapi32.CredFree(arr)

    return found


def _refresh_antigravity_cli_token() -> bool:
    """Ask the local CLI to refresh its expired Windows Credential Manager token (no window, no AI tokens)."""
    exe = shutil.which('agy')
    if not exe:
        candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'agy' / 'bin' / 'agy.exe'
        if candidate.is_file():
            exe = str(candidate)
    if not exe:
        return False
    try:
        res = subprocess.run([exe, 'models'], capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW)
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


CLAUDE_RENEW_GAP = 300.0   # seconds between attempts to renew a Claude sign-in that could not be renewed
CLAUDE_EXPIRED = 'The Claude Code sign-in has expired. Run claude auth login to sign in again.'
# Claude Code's own sign-in server and client (its CLI's TOKEN_URL and CLIENT_ID)
CLAUDE_TOKEN_URL = 'https://platform.claude.com/v1/oauth/token'
CLAUDE_CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
CLAUDE_LOCK_STALE = 60.0   # s: Claude Code's refresh locks are stale after a minute (renewed every 5 s held)
_claude_renewal = {'at': -1e9, 'mtime': -1.0}


CLAUDE_CLI_KNOWN = '2.1.325'    # Claude Code's version when this was written, if none is installed


def _claude_cli_agent() -> str:
    """Claude Code's own User-Agent, as its CLI sends it to the sign-in server: that server answers
    any other with 429 Too Many Requests (Dockline's 'claude-code/2.1.0' never renewed a sign-in).
    The version is the newest installed (~/.local/share/claude/versions)."""
    found = []
    try:
        for entry in (Path.home() / '.local' / 'share' / 'claude' / 'versions').iterdir():
            parts = entry.name.split('.')
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                found.append(tuple(int(p) for p in parts))
    except OSError:
        pass
    version = '.'.join(map(str, max(found))) if found else CLAUDE_CLI_KNOWN
    return f'claude-cli/{version} (external, cli)'


def _claude_locks(path: Path) -> list[Path]:
    """The locks Claude Code holds while it renews its sign-in (proper-lockfile directories): one in its
    folder, and the older one beside the folder."""
    folder = path.parent
    try:
        beside = Path(str(folder.resolve()) + '.lock')
    except OSError:
        beside = Path(str(folder) + '.lock')
    return [folder / '.oauth_refresh.lock', beside]


def _take_claude_locks(path: Path) -> list[Path] | None:
    """Claude Code's renewal locks, all taken, or None while Claude Code is renewing (a lock held and
    fresh). A stale lock, left by a process that ended, is taken over."""
    taken: list[Path] = []
    for lock in _claude_locks(path):
        try:
            lock.mkdir()
        except FileExistsError:
            try:
                fresh = time.time() - lock.stat().st_mtime < CLAUDE_LOCK_STALE
            except OSError:
                fresh = False
            if fresh:
                _give_claude_locks(taken)
                return None
            os.utime(lock)                                    # a dead holder's: taken over, as Claude Code does
        except OSError:
            _give_claude_locks(taken)
            return None
        taken.append(lock)
    return taken


def _give_claude_locks(locks: list[Path]) -> None:
    for lock in reversed(locks):
        try:
            lock.rmdir()
        except OSError:
            pass


def _refresh_claude_cli_token(path: Path | None = None, post=None) -> bool:
    """Renew Claude Code's expired sign-in in ~/.claude/.credentials.json as Claude Code itself does:
    its refresh grant to its sign-in server, under its own refresh locks, the new tokens written back
    with everything else in the file kept. Claude Code in the Claude desktop app is signed in by the app
    and never renews that file, so it expires 8 hours after the CLI was last used, and `claude auth
    status` does not renew it. An attempt that renews nothing waits CLAUDE_RENEW_GAP before the next,
    unless the file changes meanwhile. True once the file holds a new token."""
    path = path or claude_credentials_path()
    post = post or _http_json
    mtime = _stat_mtime(path)
    mono = time.monotonic()
    if mono - _claude_renewal['at'] < CLAUDE_RENEW_GAP and mtime == _claude_renewal['mtime']:
        return False
    _claude_renewal['at'] = mono
    _claude_renewal['mtime'] = mtime
    locks = _take_claude_locks(path)
    if locks is None:
        return False                                          # Claude Code is renewing it right now
    try:
        try:
            raw = json.loads(path.read_text('utf-8'))
        except (OSError, ValueError):
            return False
        oauth = raw.get('claudeAiOauth') if isinstance(raw, dict) else None
        if not isinstance(oauth, dict) or not oauth.get('refreshToken'):
            return False
        try:
            if float(oauth.get('expiresAt') or 0) / 1000 > time.time() + 60:
                return _stat_mtime(path) != mtime             # renewed by Claude Code meanwhile
        except (TypeError, ValueError):
            pass
        body = {'grant_type': 'refresh_token', 'refresh_token': oauth['refreshToken'],
                'client_id': oauth.get('clientId') or CLAUDE_CLIENT_ID}
        if isinstance(oauth.get('scopes'), list) and oauth['scopes']:
            body['scope'] = ' '.join(str(s) for s in oauth['scopes'])
        try:
            answer = post(CLAUDE_TOKEN_URL, method='POST', body=body, timeout=15,
                          headers={'User-Agent': _claude_cli_agent()})
        except Exception:
            return False
        token = answer.get('access_token') if isinstance(answer, dict) else None
        try:
            lasts = float(answer.get('expires_in'))
        except (AttributeError, TypeError, ValueError):
            return False
        if not token or lasts <= 0:
            return False
        now_ms = time.time() * 1000
        oauth['accessToken'] = str(token)
        oauth['refreshToken'] = str(answer.get('refresh_token') or oauth['refreshToken'])
        oauth['expiresAt'] = int(now_ms + lasts * 1000)
        try:
            oauth['refreshTokenExpiresAt'] = int(now_ms + float(answer['refresh_token_expires_in']) * 1000)
        except (KeyError, TypeError, ValueError):
            pass
        if isinstance(answer.get('scope'), str) and answer['scope'].split():
            oauth['scopes'] = answer['scope'].split()
        spare = path.with_name(path.name + '.dockline')
        try:
            spare.write_text(json.dumps(raw, separators=(',', ':')), 'utf-8')
            os.replace(spare, path)
        except OSError:
            try:
                spare.unlink()
            except OSError:
                pass
            return False
        _claude_renewal['mtime'] = _stat_mtime(path)
        return True
    finally:
        _give_claude_locks(locks)


def _claude_sign_in(path: Path) -> tuple[str, str, float | None] | None:
    """The access token, plan and expiry (epoch seconds) in Claude Code's credentials file."""
    try:
        raw = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return None
    oauth = raw.get('claudeAiOauth') if isinstance(raw, dict) else None
    if not isinstance(oauth, dict):
        return '', '', None
    expires = oauth.get('expiresAt')
    try:
        expires = float(expires) / 1000 if expires else None
    except (TypeError, ValueError):
        expires = None
    return str(oauth.get('accessToken') or ''), str(oauth.get('subscriptionType') or '').strip(), expires


def _empty_state(signed_in: bool = False, error: str = '') -> dict:
    return {
        'connected': False,
        'signed_in': signed_in,
        'plan': '',
        'windows': [],
        'updated_at': None,
        'error': error,
    }


def _http_json(url: str, method: str = 'GET', headers: dict | None = None,
               body: dict | None = None, timeout: int = 8) -> dict:
    payload = json.dumps(body).encode('utf-8') if body is not None else None
    req_headers = {'Accept': 'application/json', 'Accept-Encoding': 'gzip', **(headers or {})}
    if payload is not None:
        req_headers.setdefault('Content-Type', 'application/json')
    request = urllib.request.Request(url, data=payload, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_bytes = response.read()
            enc = (response.headers.get('Content-Encoding') or '').lower() if response.headers else ''
            if 'gzip' in enc or raw_bytes[:2] == b'\x1f\x8b':
                raw_bytes = gzip.decompress(raw_bytes)
            raw = raw_bytes.decode('utf-8', errors='replace')
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        if err.code == 429:
            retry = None
            try:
                hdr = err.headers.get('Retry-After') if err.headers else None
                if hdr is not None:
                    retry = max(15.0, min(600.0, float(hdr)))
            except (TypeError, ValueError):
                retry = None
            raise _RateLimited(retry) from None
        raise _HttpStatus(err.code) from None


# ---------------------------------------------------------------- payload parsers

def parse_claude_usage(data: dict) -> list[dict]:
    """Parse Anthropic's /api/oauth/usage payload into normalized windows."""
    if not isinstance(data, dict):
        return []
    windows = []
    mapping = (
        ('five_hour', 'session', 'Session'),
        ('seven_day', 'weekly', 'Weekly'),
        ('seven_day_sonnet', 'sonnet', 'Sonnet'),
        ('seven_day_opus', 'opus', 'Opus'),
    )
    for field, key, label in mapping:
        entry = data.get(field)
        if not isinstance(entry, dict):
            continue
        raw_used = entry.get('utilization')
        if raw_used is None:
            raw_used = entry.get('used_percentage')
        if raw_used is None:
            continue
        try:
            used = max(0.0, min(100.0, float(raw_used)))
        except (TypeError, ValueError):
            continue
        windows.append({
            'key': key,
            'label': label,
            'used': used,
            'resets_at': parse_time(entry.get('resets_at')),
        })
    return windows


def _window_from_codex(entry: dict | None, key: str, fallback_label: str, now: float) -> dict | None:
    if not isinstance(entry, dict):
        return None
    raw_used = entry.get('used_percent')
    if raw_used is None:
        raw_used = entry.get('utilization')
    if raw_used is None:
        return None
    try:
        used = max(0.0, min(100.0, float(raw_used)))
    except (TypeError, ValueError):
        return None
    resets_at = parse_time(entry.get('reset_at') or entry.get('resets_at'))
    if resets_at is None and entry.get('reset_after_seconds') is not None:
        try:
            resets_at = now + max(0.0, float(entry['reset_after_seconds']))
        except (TypeError, ValueError):
            resets_at = None
    return {'key': key, 'label': fallback_label, 'used': used, 'resets_at': resets_at}


def parse_chatgpt_usage(data: dict, now: float | None = None) -> tuple[list[dict], str]:
    """Parse ChatGPT Codex's /backend-api/wham/usage response."""
    if not isinstance(data, dict):
        return [], ''
    current = time.time() if now is None else now
    plan = str(data.get('plan_type') or '').strip()
    rate = data.get('rate_limit') if isinstance(data.get('rate_limit'), dict) else data
    if not plan and isinstance(rate, dict):
        plan = str(rate.get('plan_type') or '').strip()
    windows = []
    if isinstance(rate, dict):
        primary = _window_from_codex(rate.get('primary_window') or rate.get('primary'), 'session', 'Session', current)
        secondary = _window_from_codex(rate.get('secondary_window') or rate.get('secondary'), 'weekly', 'Weekly', current)
        if primary:
            windows.append(primary)
        if secondary:
            windows.append(secondary)
    return windows, plan.capitalize() if plan else ''


def parse_codex_session_limits(rate_limits: dict, now: float | None = None) -> tuple[list[dict], str]:
    """Parse a rate_limits block from ~/.codex/sessions/*/*/*/*.jsonl."""
    return parse_chatgpt_usage(rate_limits, now=now)


def _newest_subdirs(folder_path: Path, limit: int = 3) -> list[Path]:
    try:
        dirs = [entry for entry in os.scandir(folder_path) if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return []

    def mtime_of(entry: os.DirEntry) -> float:
        try:
            return entry.stat(follow_symlinks=False).st_mtime
        except OSError:
            return 0.0

    if dirs and all(d.name.isdigit() for d in dirs):
        dirs.sort(key=lambda e: (e.name, mtime_of(e)), reverse=True)
    else:
        dirs.sort(key=lambda e: (mtime_of(e), e.name), reverse=True)
    return [Path(d.path) for d in dirs[:limit]]


def _newest_files_in(folder_path: Path, suffix: str = '', limit: int = 4) -> list[tuple[float, Path]]:
    out: list[tuple[float, Path]] = []
    try:
        for entry in os.scandir(folder_path):
            if entry.is_file(follow_symlinks=False) and (not suffix or entry.name.endswith(suffix)):
                try:
                    out.append((entry.stat(follow_symlinks=False).st_mtime, Path(entry.path)))
                except OSError:
                    continue
    except OSError:
        return []
    out.sort(key=lambda item: item[0], reverse=True)
    return out[:limit]


def _newest_codex_rollouts(sessions_dir: Path, limit: int = 4) -> list[tuple[float, Path]]:
    """Find the most recent Codex rollout JSONL files without scanning entire historic archives."""
    if not sessions_dir.is_dir():
        return []
    candidates: list[tuple[float, Path]] = _newest_files_in(sessions_dir, '.jsonl', limit)
    for lvl1 in _newest_subdirs(sessions_dir, 2):
        candidates.extend(_newest_files_in(lvl1, '.jsonl', limit))
        for lvl2 in _newest_subdirs(lvl1, 2):
            candidates.extend(_newest_files_in(lvl2, '.jsonl', limit))
            for lvl3 in _newest_subdirs(lvl2, 3):
                candidates.extend(_newest_files_in(lvl3, '.jsonl', limit))
    candidates.sort(key=lambda item: item[0], reverse=True)
    seen: set[Path] = set()
    ordered: list[tuple[float, Path]] = []
    for mtime, path in candidates:
        if path not in seen:
            seen.add(path)
            ordered.append((mtime, path))
            if len(ordered) >= limit:
                break
    return ordered


def latest_codex_session_limits(sessions_dir: Path) -> tuple[list[dict], str, float | None]:
    """Read the newest token_count rate_limits from local Codex rollout logs without network traffic."""
    for mtime, file in _newest_codex_rollouts(sessions_dir, limit=4):
        try:
            with file.open('rb') as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 131072))
                lines = handle.read().decode('utf-8', errors='replace').splitlines()
        except OSError:
            continue
        for raw in reversed(lines):
            if '"rate_limits"' not in raw:
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            payload = event.get('payload') if isinstance(event, dict) else None
            limits = payload.get('rate_limits') if isinstance(payload, dict) else None
            if isinstance(limits, dict):
                windows, plan = parse_codex_session_limits(limits, now=mtime)
                if windows:
                    stamp = parse_time(event.get('timestamp')) or mtime
                    return windows, plan, stamp
    return [], '', None


def parse_gemini_quota_summary(data: dict) -> list[dict]:
    """Parse Antigravity / Cloud Code retrieveUserQuotaSummary groups into 5-hour session and weekly windows."""
    if not isinstance(data, dict):
        return []
    groups = data.get('groups')
    if not isinstance(groups, list) or not groups:
        return []
    gem_5h = gem_wk = third_5h = third_wk = None
    extras: list[dict] = []

    for group in groups:
        if not isinstance(group, dict):
            continue
        gname = str(group.get('displayName') or '').strip()
        glow = gname.lower()
        buckets = group.get('buckets')
        if not isinstance(buckets, list):
            continue
        for item in buckets:
            if not isinstance(item, dict) or item.get('remainingFraction') is None:
                continue
            try:
                rem = max(0.0, min(1.0, float(item['remainingFraction'])))
            except (TypeError, ValueError):
                continue
            used = round((1.0 - rem) * 100.0, 1)
            resets_at = parse_time(item.get('resetTime'))
            bid = str(item.get('bucketId') or '').strip().lower()
            wkind = str(item.get('window') or '').strip().lower()
            dname = str(item.get('displayName') or '').strip()
            is_weekly = 'week' in bid or 'week' in wkind or 'week' in dname.lower()
            is_gemini = bid.startswith('gemini') or 'gemini' in glow
            is_third = bid.startswith('3p') or 'claude' in glow or 'gpt' in glow

            if is_gemini and not is_weekly and gem_5h is None:
                gem_5h = {'key': 'session', 'label': 'Session', 'used': used, 'resets_at': resets_at,
                          'detail': gname or 'Gemini Models'}
            elif is_gemini and is_weekly and gem_wk is None:
                gem_wk = {'key': 'weekly', 'label': 'Weekly', 'used': used, 'resets_at': resets_at,
                          'detail': gname or 'Gemini Models'}
            elif is_third and not is_weekly and third_5h is None:
                third_5h = {'key': 'agy_claude_5h', 'label': 'Claude 5h', 'used': used, 'resets_at': resets_at,
                            'detail': gname or 'Claude and GPT models'}
            elif is_third and is_weekly and third_wk is None:
                third_wk = {'key': 'agy_claude_weekly', 'label': 'Claude Wk', 'used': used, 'resets_at': resets_at,
                            'detail': gname or 'Claude and GPT models'}
            else:
                short = (gname.split()[0] if gname else 'Pool')[:7]
                lbl = f'{short} Wk' if is_weekly else f'{short} 5h'
                extras.append({'key': bid or f'extra_{len(extras)}', 'label': lbl, 'used': used,
                               'resets_at': resets_at, 'detail': gname})

    windows = [w for w in (gem_5h, gem_wk, third_5h, third_wk) if w is not None]
    windows.extend(extras)
    return windows


def parse_gemini_quota(data: dict, antigravity: bool = False) -> list[dict]:
    """Parse Cloud Code / Gemini CLI / Antigravity retrieveUserQuota buckets into quota windows."""
    if not isinstance(data, dict):
        return []
    buckets = data.get('buckets')
    if not isinstance(buckets, list) or not buckets:
        return []
    parsed = []
    for item in buckets:
        if not isinstance(item, dict) or item.get('remainingFraction') is None:
            continue
        try:
            rem = max(0.0, min(1.0, float(item['remainingFraction'])))
        except (TypeError, ValueError):
            continue
        model = str(item.get('modelId') or '').strip()
        parsed.append({
            'model': model,
            'low': model.lower(),
            'used': round((1.0 - rem) * 100.0, 1),
            'rem': rem,
            'resets_at': parse_time(item.get('resetTime')),
        })
    if not parsed:
        return []

    # Ignore internal IDE tab-autocomplete buckets (tab_*, chat_*) when real gemini-* buckets exist
    gemini_only = [b for b in parsed if b['low'].startswith('gemini')]
    pool = gemini_only or [b for b in parsed if not b['low'].startswith(('tab_', 'chat_'))] or parsed

    def pick_worst(candidates: list[dict]) -> dict:
        return min(
            candidates,
            key=lambda b: (
                round(b['rem'], 4),
                1 if b['resets_at'] is None else 0,
                1 if ('lite' in b['low'] or 'image' in b['low']) else 0,
            ),
        )

    pro = [b for b in pool if 'pro' in b['low'] or 'carbon' in b['low'] or 'hydrogen' in b['low']]
    flash = [b for b in pool if 'flash' in b['low'] or 'argon' in b['low']]
    windows = []
    if pro:
        worst = pick_worst(pro)
        windows.append({'key': 'pro', 'label': 'Pro', 'used': worst['used'], 'resets_at': worst['resets_at']})
    if flash:
        worst = pick_worst(flash)
        windows.append({'key': 'flash', 'label': 'Flash', 'used': worst['used'], 'resets_at': worst['resets_at']})
    if antigravity:
        agy_claude = [b for b in parsed if b['low'].startswith('claude') and b['resets_at'] is not None]
        if agy_claude:
            worst_c = pick_worst(agy_claude)
            windows.append({'key': 'agy_claude', 'label': 'Claude (AGY)', 'used': worst_c['used'],
                            'resets_at': worst_c['resets_at']})
    if not windows:
        for index, bucket in enumerate(sorted(pool, key=lambda b: (b['rem'], b['resets_at'] is None))[:2]):
            label = bucket['model'].replace('gemini-', '').title()[:10] or f'Pool {index + 1}'
            windows.append({'key': f'pool_{index}', 'label': label, 'used': bucket['used'],
                            'resets_at': bucket['resets_at']})
    return windows


# ---------------------------------------------------------------- provider fetchers

def fetch_claude(home: Path | None = None, opener=_http_json, renew=None) -> dict:
    """renew: renews an expired sign-in, True once the file changed (the CLI's, for the real home;
    none for a test's home)."""
    path = claude_credentials_path(home)
    if not path.is_file():
        return _empty_state(False)
    if renew is None:
        renew = (lambda: _refresh_claude_cli_token(path)) if home is None else (lambda: False)
    found = _claude_sign_in(path)
    if found is None:
        return _empty_state(False, 'Could not read Claude credentials.')
    renewed = False
    if found[0] and found[2] is not None and found[2] <= time.time() + 60:     # expired, or about to
        renewed = True
        if renew():
            found = _claude_sign_in(path) or found
        elif found[2] <= time.time():
            # not renewed, and expired: the usage server answers such a token with 429 Too Many
            # Requests, not 401, which read as a rate limit kept a 13-hour-old reading without a word
            return _empty_state(True, CLAUDE_EXPIRED)
    token, plan_raw, _ = found
    if not token:
        return _empty_state(False)
    plan = plan_raw.capitalize() if plan_raw else ''
    payload = None
    for _attempt in range(2):
        try:
            payload = opener(
                CLAUDE_USAGE_URL,
                headers={
                    'Authorization': f'Bearer {token}',
                    'anthropic-beta': 'oauth-2025-04-20',
                    'User-Agent': 'claude-code/2.1.0',
                },
            )
            break
        except _RateLimited:
            raise
        except _HttpStatus as err:
            if err.code not in (401, 403):
                return _empty_state(True, f'Claude usage unavailable ({err}).')
            if renewed or not renew():
                return _empty_state(True, CLAUDE_EXPIRED)
            renewed = True
            again = _claude_sign_in(path)
            if not again or not again[0]:
                return _empty_state(True, CLAUDE_EXPIRED)
            token = again[0]
        except Exception:
            return _empty_state(True, 'Could not reach Claude right now.')
    windows = parse_claude_usage(payload)
    if not windows:
        return _empty_state(True, 'No active plan window reported by Claude.')
    return {
        'connected': True,
        'signed_in': True,
        'plan': plan,
        'windows': windows,
        'updated_at': time.time(),
        'error': '',
    }


def fetch_chatgpt(home: Path | None = None, opener=_http_json) -> dict:
    codex_dir = codex_home_path(home)
    auth_file = codex_dir / 'auth.json'
    local_windows, local_plan, local_stamp = latest_codex_session_limits(codex_dir / 'sessions')
    token = ''
    account_id = ''
    if auth_file.is_file():
        try:
            raw = json.loads(auth_file.read_text('utf-8'))
            if isinstance(raw, dict):
                tokens = raw.get('tokens') if isinstance(raw.get('tokens'), dict) else raw
                token = str(tokens.get('access_token') or raw.get('access_token') or '').strip()
                account_id = str(tokens.get('account_id') or raw.get('account_id') or '').strip()
        except (OSError, ValueError):
            pass
    if token:
        headers = {
            'Authorization': f'Bearer {token}',
            'User-Agent': 'codex-cli/0.1.0',
        }
        if account_id:
            headers['ChatGPT-Account-Id'] = account_id
        try:
            payload = opener(CHATGPT_USAGE_URL, headers=headers)
            windows, plan = parse_chatgpt_usage(payload)
            if windows:
                return {
                    'connected': True,
                    'signed_in': True,
                    'plan': plan or local_plan,
                    'windows': windows,
                    'updated_at': time.time(),
                    'error': '',
                }
        except _RateLimited:
            if local_windows:
                return {
                    'connected': True,
                    'signed_in': True,
                    'plan': local_plan,
                    'windows': local_windows,
                    'updated_at': local_stamp or time.time(),
                    'error': '',
                }
            raise
        except _HttpStatus as err:
            if local_windows:
                return {
                    'connected': True,
                    'signed_in': True,
                    'plan': local_plan,
                    'windows': local_windows,
                    'updated_at': local_stamp or time.time(),
                    'error': '',
                }
            if err.code in (401, 403):
                return _empty_state(True, 'ChatGPT session expired — run codex login to refresh.')
            return _empty_state(True, f'ChatGPT usage unavailable ({err}).')
        except Exception:
            if local_windows:
                return {
                    'connected': True,
                    'signed_in': True,
                    'plan': local_plan,
                    'windows': local_windows,
                    'updated_at': local_stamp or time.time(),
                    'error': '',
                }
            return _empty_state(True, 'Could not reach ChatGPT right now.')
    if local_windows:
        return {
            'connected': True,
            'signed_in': True,
            'plan': local_plan,
            'windows': local_windows,
            'updated_at': local_stamp or time.time(),
            'error': '',
        }
    return _empty_state(bool(token))


_GEMINI_PROJECT_CACHE: dict[tuple[str, str], tuple[str | None, str, str, str]] = {}


def _query_gemini_with_token(token: str, source: str = '', opener=_http_json) -> tuple[list[dict], str]:
    is_agy = 'antigravity' in source.lower()
    cache_key = (source, token[:24])
    if opener is _http_json and cache_key in _GEMINI_PROJECT_CACHE:
        cached_proj, cached_quota_url, cached_ua, cached_label = _GEMINI_PROJECT_CACHE[cache_key]
        try:
            payload = opener(
                cached_quota_url,
                method='POST',
                headers={'Authorization': f'Bearer {token}', 'User-Agent': cached_ua},
                body={'project': cached_proj} if cached_proj else {},
            )
            windows = parse_gemini_quota_summary(payload)
            if windows and any(w['key'] in ('session', 'weekly') for w in windows):
                return windows, cached_label
            if not windows:
                windows = parse_gemini_quota(payload, antigravity=is_agy)
                if windows:
                    return windows, cached_label
        except _RateLimited:
            raise
        except _HttpStatus as err:
            _GEMINI_PROJECT_CACHE.pop(cache_key, None)
            if err.code == 401:
                raise
        except Exception:
            _GEMINI_PROJECT_CACHE.pop(cache_key, None)

    endpoints = (
        ((ANTIGRAVITY_LOAD_URL, ANTIGRAVITY_SUMMARY_URL, ANTIGRAVITY_QUOTA_URL, ANTIGRAVITY_UA, 'ANTIGRAVITY', 'Antigravity'),
         (GEMINI_LOAD_URL, GEMINI_SUMMARY_URL, GEMINI_QUOTA_URL, ANTIGRAVITY_UA, 'IDE_UNSPECIFIED', 'Antigravity'))
        if is_agy else
        ((GEMINI_LOAD_URL, None, GEMINI_QUOTA_URL, 'GeminiCLI/0.1.0', 'IDE_UNSPECIFIED', ''),
         (GEMINI_LOAD_URL, None, GEMINI_QUOTA_URL, 'antigravity/1.0', 'IDE_UNSPECIFIED', ''))
    )
    last_http: _HttpStatus | None = None

    for load_url, summary_url, quota_url, ua, ide_type, plan_label in endpoints:
        headers = {
            'Authorization': f'Bearer {token}',
            'User-Agent': ua,
        }
        project = None
        try:
            assist = opener(
                load_url,
                method='POST',
                headers=headers,
                body={'metadata': {'ideType': ide_type, 'platform': 'PLATFORM_UNSPECIFIED',
                                   'pluginType': 'GEMINI'}},
            )
            if isinstance(assist, dict):
                raw_proj = assist.get('cloudaicompanionProject')
                if isinstance(raw_proj, dict):
                    raw_proj = raw_proj.get('id')
                if isinstance(raw_proj, str) and raw_proj.strip():
                    project = raw_proj.strip()
        except _RateLimited:
            raise
        except _HttpStatus as err:
            last_http = err
            if err.code == 401:
                raise
        except Exception:
            pass

        bodies = [{'project': project}, {}] if project else [{}]
        summary_partial: list[dict] = []
        if summary_url:
            for body in bodies:
                try:
                    spayload = opener(summary_url, method='POST', headers=headers, body=body)
                    swin = parse_gemini_quota_summary(spayload)
                    if swin and any(w['key'] in ('session', 'weekly') for w in swin):
                        if opener is _http_json:
                            _GEMINI_PROJECT_CACHE[cache_key] = (body.get('project'), summary_url, ua, plan_label)
                        return swin, plan_label
                    if swin and not summary_partial:
                        summary_partial = swin
                    # Support mocks that return retrieveUserQuota's buckets shape for any host URL
                    fallback_win = parse_gemini_quota(spayload, antigravity=is_agy)
                    if fallback_win and not swin:
                        return fallback_win, plan_label
                except _RateLimited:
                    raise
                except _HttpStatus as err:
                    last_http = err
                    if err.code == 401:
                        raise
                except Exception:
                    pass

        for body in bodies:
            try:
                payload = opener(quota_url, method='POST', headers=headers, body=body)
                windows = parse_gemini_quota(payload, antigravity=is_agy)
                if windows:
                    if summary_partial:
                        gem_only = [w for w in windows if w['key'] != 'agy_claude']
                        merged = gem_only[:1] + summary_partial
                        return merged, plan_label
                    if opener is _http_json:
                        _GEMINI_PROJECT_CACHE[cache_key] = (body.get('project'), quota_url, ua, plan_label)
                    return windows, plan_label
            except _RateLimited:
                raise
            except _HttpStatus as err:
                last_http = err
                if err.code == 401:
                    raise

    if last_http is not None:
        raise last_http
    return [], ''


def fetch_gemini(home: Path | None = None, opener=_http_json, cred_reader=None) -> dict:
    """Read Gemini quota from ~/.gemini/oauth_creds.json or Windows Credential Manager."""
    candidates: list[tuple[str, str, float | None]] = []

    path = gemini_credentials_path(home)
    if path.is_file():
        try:
            raw = json.loads(path.read_text('utf-8'))
            tok, exp = _extract_gemini_token(raw)
            if tok:
                candidates.append(('file', tok, exp))
        except (OSError, ValueError):
            pass

    reader = cred_reader if cred_reader is not None else (read_windows_gemini_credentials if home is None else None)
    if reader is not None:
        for target, tok, exp, _mtime in reader():
            candidates.append((target, tok, exp))

    if not candidates:
        return _empty_state(False)

    last_error = ''
    refreshed_agy = False
    now = time.time()

    for source, token, expiry in candidates:
        # Proactively refresh an expired Antigravity CLI token before making a doomed 401 request
        if source == 'gemini:antigravity' and expiry is not None and expiry <= now + 30 and not refreshed_agy \
                and cred_reader is None and home is None:
            refreshed_agy = True
            if _refresh_antigravity_cli_token():
                for t2, tok2, _exp2, _m2 in read_windows_gemini_credentials():
                    if t2 == source and tok2:
                        token = tok2
                        break
        try:
            windows, plan_label = _query_gemini_with_token(token, source=source, opener=opener)
            if windows:
                return {
                    'connected': True,
                    'signed_in': True,
                    'plan': plan_label,
                    'windows': windows,
                    'updated_at': time.time(),
                    'error': '',
                }
            last_error = 'No quota buckets reported by Gemini.'
        except _RateLimited:
            raise
        except _HttpStatus as err:
            if err.code == 401 and source == 'gemini:antigravity' and not refreshed_agy \
                    and cred_reader is None and home is None:
                refreshed_agy = True
                if _refresh_antigravity_cli_token():
                    for t2, tok2, _exp2, _m2 in read_windows_gemini_credentials():
                        if t2 == source and tok2:
                            try:
                                windows, plan_label = _query_gemini_with_token(tok2, source=source, opener=opener)
                                if windows:
                                    return {
                                        'connected': True,
                                        'signed_in': True,
                                        'plan': plan_label,
                                        'windows': windows,
                                        'updated_at': time.time(),
                                        'error': '',
                                    }
                            except Exception:
                                pass
            if err.code == 401:
                last_error = 'Gemini token expired — open Gemini CLI once to refresh your sign-in.'
            else:
                last_error = f'Gemini quota unavailable ({err}).'
        except Exception:
            last_error = 'Could not reach Gemini right now.'

    return _empty_state(True, last_error or 'Could not read Gemini quota.')


FETCHERS = {
    'claude': fetch_claude,
    'chatgpt': fetch_chatgpt,
    'gemini': fetch_gemini,
}


def sample_snapshot(now: float | None = None) -> dict[str, dict]:
    """Sample data used only in --self-test / --render and widget-picker previews when unconnected."""
    base = time.time() if now is None else now
    return {
        'claude': {
            'connected': True,
            'signed_in': True,
            'plan': 'Pro',
            'windows': [
                {'key': 'session', 'label': 'Session', 'used': 28.0, 'resets_at': base + 2 * 3600 + 14 * 60},
                {'key': 'weekly', 'label': 'Weekly', 'used': 44.0, 'resets_at': base + 4 * 86400 + 3 * 3600},
            ],
            'updated_at': base,
            'error': '',
        },
        'chatgpt': {
            'connected': True,
            'signed_in': True,
            'plan': 'Plus',
            'windows': [
                {'key': 'session', 'label': 'Session', 'used': 34.0, 'resets_at': base + 2 * 3600 + 30 * 60},
                {'key': 'weekly', 'label': 'Weekly', 'used': 21.0, 'resets_at': base + 3 * 86400 + 6 * 3600},
            ],
            'updated_at': base,
            'error': '',
        },
        'gemini': {
            'connected': True,
            'signed_in': True,
            'plan': 'Antigravity',
            'windows': [
                {'key': 'session', 'label': 'Session', 'used': 18.0, 'resets_at': base + 2 * 3600 + 50 * 60},
                {'key': 'weekly', 'label': 'Weekly', 'used': 11.0, 'resets_at': base + 6 * 86400 + 16 * 3600},
                {'key': 'agy_claude_5h', 'label': 'Claude 5h', 'used': 6.0, 'resets_at': base + 4 * 3600 + 55 * 60},
                {'key': 'agy_claude_weekly', 'label': 'Claude Wk', 'used': 4.0, 'resets_at': base + 6 * 86400 + 16 * 3600},
            ],
            'updated_at': base,
            'error': '',
        },
    }


# ---------------------------------------------------------------- background coordinator

def _stat_mtime(path: Path | None) -> float:
    if path is None:
        return 0.0
    try:
        return path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        return 0.0


class AIUsage(QObject):
    changed = Signal(dict)
    busy_changed = Signal(bool)
    low_allowance = Signal(str, str, float)     # provider name, window label, remaining %

    def __init__(self, cfg, testing: bool = False, home: Path | None = None, opener=_http_json):
        super().__init__()
        self.cfg = cfg
        self.testing = testing
        self.home = home
        self.opener = opener
        self._lock = threading.Lock()
        self._busy = False
        self._queued_force = False
        self._queued_providers: set[str] = set()
        self._stopped = False
        self._last_poll = 0.0
        self._last_watch = 0.0
        self._last_step = 0.0
        self._last_card_open = False
        self._last_win_cred_check = -999.0
        self._cached_win_cred_mtime = 0.0
        self._provider_polled_at: dict[str, float] = {}
        self._backoff_until: dict[str, float] = {}
        self._cred_mtimes: dict[str, float] = {}
        self._act_mtimes: dict[str, float] = {}
        self._active_files: dict[str, Path] = {}
        self._pending_live: set[str] = set()
        self._first_pending: dict[str, float] = {}
        self._last_activity: dict[str, float] = {}
        self._reset_refetched: set[tuple[str, str, int]] = set()
        self._warned: set[tuple[str, str, int]] = set()
        self._watcher: QFileSystemWatcher | None = None
        self._fs_debounce: QTimer | None = None
        self._live_timer: QTimer | None = None
        self.latest: dict[str, dict] = {p: _empty_state(False) for p in PROVIDERS}
        if testing:
            self.latest = sample_snapshot()
        else:
            self._load_cache()
            creds, acts, _ = self._scan_local_state(time.monotonic())
            self._cred_mtimes = creds
            self._act_mtimes = acts

    @property
    def is_busy(self) -> bool:
        return self._busy

    def refresh_mode(self) -> str:
        return normalize_refresh_mode(self.cfg.get('ai_refresh'))

    def live_watching_enabled(self) -> bool:
        return self.refresh_mode() in ('live', '30s')

    def active_providers(self) -> set[str]:
        enabled = set(self.cfg.get('ai_enabled') or PROVIDERS)
        individual = set(self.cfg.get('widgets') or ())
        return (enabled | (individual & set(PROVIDERS))) & set(PROVIDERS)

    def cache_path(self) -> Path:
        return folder() / 'ai-usage-cache.json'

    def _load_cache(self) -> None:
        path = self.cache_path()
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text('utf-8'))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for provider in PROVIDERS:
            entry = raw.get(provider)
            if not isinstance(entry, dict) or not isinstance(entry.get('windows'), list) or not entry['windows']:
                continue
            if provider == 'gemini' and any(isinstance(w, dict) and w.get('key') == 'agy_claude' for w in entry['windows']):
                continue
            self.latest[provider] = {
                'connected': True,
                'signed_in': True,
                'plan': str(entry.get('plan') or ''),
                'windows': entry['windows'],
                'updated_at': parse_time(entry.get('updated_at')),
                'error': '',
            }

    def _save_cache(self) -> None:
        if self.testing:
            return
        out = {}
        for provider, state in self.latest.items():
            if state.get('connected') and state.get('windows'):
                out[provider] = {
                    'plan': state.get('plan', ''),
                    'windows': state['windows'],
                    'updated_at': state.get('updated_at'),
                }
        try:
            path = self.cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            spare = path.with_suffix('.tmp')
            spare.write_text(json.dumps(out, indent=2), 'utf-8')
            os.replace(spare, path)
        except OSError:
            pass

    def _scan_local_state(self, mono: float | None = None) -> tuple[dict[str, float], dict[str, float], list[str]]:
        """Inspect credential files and recent local CLI session logs in sub-millisecond bounded time."""
        now_m = time.monotonic() if mono is None else mono
        creds: dict[str, float] = {}
        acts: dict[str, float] = {}
        watch_dirs: list[Path] = []

        # 1. Claude Code (~/.claude)
        claude_cred = claude_credentials_path(self.home)
        claude_dir = claude_cred.parent
        c_cred_t = _stat_mtime(claude_cred)
        if c_cred_t:
            creds['claude'] = c_cred_t
        c_act = max(
            _stat_mtime(claude_dir / 'history.jsonl'),
            _stat_mtime(claude_dir / 'stats-cache.json'),
            _stat_mtime(self._active_files.get('claude')),
        )
        c_newest_path = self._active_files.get('claude')
        proj_root = claude_dir / 'projects'
        proj_subs = _newest_subdirs(proj_root, 4)
        for sub in proj_subs:
            for mt, fp in _newest_files_in(sub, '.jsonl', 2):
                if mt > c_act:
                    c_act = mt
                    c_newest_path = fp
        if c_newest_path is not None:
            self._active_files['claude'] = c_newest_path
        if c_act:
            acts['claude'] = c_act
        watch_dirs.extend([claude_dir, proj_root, *proj_subs[:2]])
        if c_newest_path is not None:
            watch_dirs.append(c_newest_path.parent)

        # 2. OpenAI Codex / ChatGPT (~/.codex)
        codex_dir = codex_home_path(self.home)
        auth_t = _stat_mtime(codex_dir / 'auth.json')
        if auth_t:
            creds['chatgpt'] = auth_t
        x_act = max(
            _stat_mtime(codex_dir / 'history.jsonl'),
            _stat_mtime(self._active_files.get('chatgpt')),
        )
        x_newest_path = self._active_files.get('chatgpt')
        sessions_root = codex_dir / 'sessions'
        for mt, fp in _newest_codex_rollouts(sessions_root, limit=2):
            if mt > x_act:
                x_act = mt
                x_newest_path = fp
        if x_newest_path is not None:
            self._active_files['chatgpt'] = x_newest_path
        if x_act:
            acts['chatgpt'] = x_act
        watch_dirs.extend([codex_dir, sessions_root])
        if x_newest_path is not None:
            watch_dirs.append(x_newest_path.parent)

        # 3. Gemini CLI & Antigravity (~/.gemini + Windows Credential Manager)
        gem_cred = gemini_credentials_path(self.home)
        gem_dir = gem_cred.parent
        g_cred_t = _stat_mtime(gem_cred)
        if self.home is None:
            if now_m - self._last_win_cred_check >= 10.0:
                self._last_win_cred_check = now_m
                self._cached_win_cred_mtime = max(
                    (written for _t, _tok, _exp, written in read_windows_gemini_credentials() if written),
                    default=0.0,
                )
            g_cred_t = max(g_cred_t, self._cached_win_cred_mtime)
        if g_cred_t:
            creds['gemini'] = g_cred_t

        g_act = _stat_mtime(self._active_files.get('gemini'))
        g_newest_path = self._active_files.get('gemini')
        agy_conv = gem_dir / 'antigravity' / 'conversations'
        for mt, fp in _newest_files_in(agy_conv, '', 2):
            if mt > g_act:
                g_act = mt
                g_newest_path = fp
        agy_brain = gem_dir / 'antigravity-cli' / 'brain'
        brain_subs = _newest_subdirs(agy_brain, 3)
        for bsub in brain_subs:
            for candidate in (
                bsub / '.system_generated' / 'logs' / 'transcript.jsonl',
                bsub / '.system_generated' / 'logs' / 'transcript_full.jsonl',
            ):
                mt = _stat_mtime(candidate)
                if mt > g_act:
                    g_act = mt
                    g_newest_path = candidate
            for mt, fp in _newest_files_in(bsub, '', 2):
                if mt > g_act:
                    g_act = mt
                    g_newest_path = fp
        gem_tmp = gem_dir / 'tmp'
        tmp_subs = _newest_subdirs(gem_tmp, 3)
        for tsub in tmp_subs:
            mt_log = _stat_mtime(tsub / 'logs.json')
            if mt_log > g_act:
                g_act = mt_log
                g_newest_path = tsub / 'logs.json'
            for mt, fp in _newest_files_in(tsub / 'chats', '.json', 2):
                if mt > g_act:
                    g_act = mt
                    g_newest_path = fp
        if g_newest_path is not None:
            self._active_files['gemini'] = g_newest_path
        if g_act:
            acts['gemini'] = g_act
        watch_dirs.extend([gem_dir, agy_conv, agy_brain, gem_tmp])
        if brain_subs:
            watch_dirs.append(brain_subs[0] / '.system_generated' / 'logs')
            watch_dirs.append(brain_subs[0])
        if g_newest_path is not None:
            watch_dirs.append(g_newest_path.parent)

        seen_paths: set[str] = set()
        existing_watch: list[str] = []
        for d in watch_dirs:
            try:
                if d.is_dir():
                    norm = str(d)
                    if norm not in seen_paths:
                        seen_paths.add(norm)
                        existing_watch.append(norm)
                        if len(existing_watch) >= 22:
                            break
            except OSError:
                continue
        return creds, acts, existing_watch

    def _sync_watcher_paths(self, desired: list[str]) -> None:
        if self._watcher is None or self._stopped:
            return
        current = set(self._watcher.directories())
        want = set(desired)
        stale = list(current - want)
        added = list(want - current)
        if stale:
            self._watcher.removePaths(stale)
        if added:
            self._watcher.addPaths(added)

    def ensure_watching(self) -> None:
        """Enable OS directory watching when Live / 30s updates are active."""
        if self.testing or self._stopped:
            return
        if not self.live_watching_enabled():
            if self._watcher is not None:
                dirs = self._watcher.directories()
                if dirs:
                    self._watcher.removePaths(dirs)
            self._pending_live.clear()
            self._first_pending.clear()
            return
        if self._watcher is None:
            self._watcher = QFileSystemWatcher(self)
            self._fs_debounce = QTimer(self)
            self._fs_debounce.setSingleShot(True)
            self._fs_debounce.setInterval(250)
            self._fs_debounce.timeout.connect(self._on_fs_activity)
            self._live_timer = QTimer(self)
            self._live_timer.setSingleShot(True)
            self._live_timer.timeout.connect(self._on_live_timer)
            self._watcher.directoryChanged.connect(self._queue_fs_check)
            self._watcher.fileChanged.connect(self._queue_fs_check)
        _, _, paths = self._scan_local_state(time.monotonic())
        self._sync_watcher_paths(paths)

    def configure(self) -> None:
        """Apply changes to ai_refresh or enabled AI services immediately."""
        if self.testing or self._stopped:
            return
        self.ensure_watching()
        interval = float(poll_interval_for(self.refresh_mode(), card_open=False))
        self.poke(max_age=interval)

    def _queue_fs_check(self, _path: str = '') -> None:
        if not self._stopped and self._fs_debounce is not None:
            self._fs_debounce.start()

    def _schedule_live_timer(self, mono: float) -> None:
        if self._stopped or self._live_timer is None or not self._pending_live:
            return
        soonest = LIVE_DEBOUNCE
        for p in self._pending_live:
            gap_wait = max(0.0, MIN_LIVE_GAP - (mono - self._provider_polled_at.get(p, -999.0)))
            debounce_wait = max(0.0, LIVE_DEBOUNCE - (mono - self._last_activity.get(p, mono)))
            soonest = min(soonest, max(gap_wait, debounce_wait, 0.25))
        self._live_timer.start(max(250, round(soonest * 1000)))

    def _on_fs_activity(self) -> None:
        if self._stopped or self.testing:
            return
        mono = time.monotonic()
        self.watch_files(now_mono=mono)
        self._schedule_live_timer(mono)

    def _on_live_timer(self) -> None:
        if self._stopped or self.testing:
            return
        mono = time.monotonic()
        self.watch_files(now_mono=mono)
        if self._pending_live:
            self._schedule_live_timer(mono)

    def _maybe_warn_low(self, provider: str, state: dict) -> tuple[str, str, float] | None:
        if state.get('connected') and state.get('windows'):
            first = state['windows'][0]
            left = remaining_pct(first)
            token_key = (provider, first['key'], round(first.get('resets_at') or 0))
            if left <= LOW_THRESHOLD and token_key not in self._warned:
                self._warned.add(token_key)
                return (INFO[provider]['name'], first['label'], left)
        return None

    def _apply_local_codex_live(self) -> bool:
        """Apply freshly written local Codex session rate_limits immediately with zero network wait."""
        if 'chatgpt' not in self.active_providers():
            return False
        local_windows, local_plan, local_stamp = latest_codex_session_limits(codex_home_path(self.home) / 'sessions')
        if not local_windows:
            return False
        prev = self.latest.get('chatgpt') or _empty_state(False)
        if prev.get('connected') and prev.get('windows') == local_windows:
            return False
        fresh = {
            'connected': True,
            'signed_in': True,
            'plan': local_plan or prev.get('plan', ''),
            'windows': local_windows,
            'updated_at': max(time.time(), local_stamp or 0.0),
            'error': '',
        }
        self.latest = {**self.latest, 'chatgpt': fresh}
        self._save_cache()
        alert = self._maybe_warn_low('chatgpt', fresh)
        if not self._stopped:
            self.changed.emit(dict(self.latest))
            if alert is not None and self.cfg.get('ai_warn_low', True):
                self.low_allowance.emit(*alert)
        return True

    def _ready_live_providers(self, mono: float) -> set[str]:
        active = self.active_providers()
        ready: set[str] = set()
        for provider in list(self._pending_live):
            if provider not in active:
                self._pending_live.discard(provider)
                self._first_pending.pop(provider, None)
                continue
            quiet = mono - self._last_activity.get(provider, mono)
            waited = mono - self._first_pending.get(provider, mono)
            since_poll = mono - self._provider_polled_at.get(provider, -999.0)
            if (quiet >= LIVE_DEBOUNCE or waited >= LIVE_MAX_WAIT) and since_poll >= MIN_LIVE_GAP:
                self._pending_live.discard(provider)
                self._first_pending.pop(provider, None)
                ready.add(provider)
        return ready

    def _expired_window_providers(self, mono: float, now_epoch: float | None = None) -> set[str]:
        """Re-fetch a provider once automatically right after one of its quota windows resets."""
        now_s = time.time() if now_epoch is None else now_epoch
        due: set[str] = set()
        for provider in self.active_providers():
            state = self.latest.get(provider) or {}
            if not state.get('connected'):
                continue
            for win in state.get('windows') or ():
                resets_at = win.get('resets_at')
                if resets_at is not None and resets_at <= now_s:
                    key = (provider, str(win.get('key', '')), round(resets_at))
                    if key not in self._reset_refetched:
                        if mono - self._provider_polled_at.get(provider, -999.0) >= MIN_LIVE_GAP:
                            self._reset_refetched.add(key)
                            due.add(provider)
        return due

    def watch_files(self, now_mono: float | None = None) -> set[str]:
        """Notice fresh sign-ins and local CLI turn activity promptly."""
        if self.testing or self._stopped:
            return set()
        mono = time.monotonic() if now_mono is None else now_mono
        self._last_watch = mono
        creds, acts, watch_paths = self._scan_local_state(mono)
        if self._watcher is not None and self.live_watching_enabled():
            self._sync_watcher_paths(watch_paths)

        active = self.active_providers()
        immediate: set[str] = set()

        for provider in PROVIDERS:
            new_cred = creds.get(provider, 0.0)
            if new_cred != self._cred_mtimes.get(provider, 0.0):
                self._cred_mtimes[provider] = new_cred
                self._backoff_until.pop(provider, None)
                if provider in active and mono - self._provider_polled_at.get(provider, -999.0) >= 2.0:
                    immediate.add(provider)

            new_act = acts.get(provider, 0.0)
            if new_act != self._act_mtimes.get(provider, 0.0):
                self._act_mtimes[provider] = new_act
                if provider == 'chatgpt' and new_act > 0:
                    self._apply_local_codex_live()
                if provider in active and new_act > 0 and self.live_watching_enabled():
                    self._first_pending.setdefault(provider, mono)
                    self._last_activity[provider] = mono
                    self._pending_live.add(provider)

        due = immediate | self._ready_live_providers(mono)
        if due:
            self.fetch(providers=due, now_mono=mono)
        return due

    def poke(self, providers=None, max_age: float = 20.0, now_mono: float | None = None) -> set[str]:
        """Refresh any active provider whose last network check is older than max_age seconds."""
        if self.testing or self._stopped:
            return set()
        mono = time.monotonic() if now_mono is None else now_mono
        active = self.active_providers()
        scope = (set(providers) & active) if providers is not None else active
        stale = {
            p for p in scope
            if p not in self._provider_polled_at or (mono - self._provider_polled_at[p]) >= max_age
        }
        if stale:
            self.fetch(providers=stale, now_mono=mono)
        return stale

    def step(self, card_open: bool = False, now_mono: float | None = None) -> set[str]:
        """Periodic coordinator heartbeat called once per second by dock AI widgets."""
        if self.testing or self._stopped:
            return set()
        mono = time.monotonic() if now_mono is None else now_mono
        if now_mono is None and (mono - self._last_step) < 0.75 and (not card_open or self._last_card_open):
            return set()
        self._last_step = mono
        self._last_card_open = card_open

        watch_iv = float(WATCH_EVERY) if self.live_watching_enabled() else 10.0
        triggered: set[str] = set()
        if self._last_watch == 0.0 or (mono - self._last_watch) >= watch_iv:
            triggered |= self.watch_files(now_mono=mono)

        due = self._ready_live_providers(mono) | self._expired_window_providers(mono)
        interval = float(poll_interval_for(self.refresh_mode(), card_open=card_open))
        active = self.active_providers()
        for provider in active:
            if provider not in self._provider_polled_at or (mono - self._provider_polled_at[provider]) >= interval:
                due.add(provider)
        due -= triggered
        if due:
            self.fetch(providers=due, now_mono=mono)
        return triggered | due

    def stop(self) -> None:
        self._stopped = True
        with self._lock:
            self._queued_providers.clear()
            self._queued_force = False
        if self._fs_debounce is not None:
            self._fs_debounce.stop()
        if self._live_timer is not None:
            self._live_timer.stop()
        if self._watcher is not None:
            dirs = self._watcher.directories()
            if dirs:
                self._watcher.removePaths(dirs)
            files = self._watcher.files()
            if files:
                self._watcher.removePaths(files)

    def fetch(self, force: bool = False, providers=None, now_mono: float | None = None) -> None:
        if self.testing or self._stopped:
            return
        active = self.active_providers()
        target = (set(providers) & active) if providers is not None else set(active)
        if not target and not force:
            return
        if not target:
            target = set(active)
        mono = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            for p in target:
                if force or mono >= self._backoff_until.get(p, 0.0):
                    self._provider_polled_at[p] = mono
            if self._busy:
                self._queued_force = self._queued_force or force
                self._queued_providers |= target
                return
            self._busy = True
        self._last_poll = mono
        if not self._stopped:
            self.busy_changed.emit(True)
        threading.Thread(target=self._run_fetch, args=(force, target, now_mono), daemon=True).start()

    def refresh_sync(self, force: bool = True, providers=None, now_mono: float | None = None) -> dict[str, dict]:
        """Synchronous refresh helper for deterministic unit tests."""
        mono = time.monotonic() if now_mono is None else now_mono
        self._last_poll = mono
        return self._collect(force=force, providers=providers, now_mono=now_mono)

    def _collect(self, force: bool = False, providers=None, now_mono: float | None = None) -> dict[str, dict]:
        mono = time.monotonic() if now_mono is None else now_mono
        updated = dict(self.latest)
        alerts: list[tuple[str, str, float]] = []
        active = self.active_providers()
        wanted = (set(providers) & active) if providers is not None else active
        for provider in PROVIDERS:
            if provider not in wanted:
                continue
            if not force and mono < self._backoff_until.get(provider, 0.0):
                continue
            self._provider_polled_at[provider] = mono
            previous = self.latest.get(provider) or _empty_state(False)
            try:
                fresh = FETCHERS[provider](home=self.home, opener=self.opener)
            except _RateLimited as exc:
                wait = exc.retry_after if exc.retry_after is not None else RATE_LIMIT_WAIT
                self._backoff_until[provider] = mono + wait
                if previous.get('connected') and previous.get('windows'):
                    old = time.time() - float(previous.get('updated_at') or 0) > RATE_LIMIT_SAY
                    updated[provider] = {**previous, 'error': RATE_LIMITED if old else ''}
                else:
                    updated[provider] = _empty_state(True, 'Rate-limited — will retry shortly.')
                continue
            if not fresh.get('connected') and fresh.get('signed_in') and previous.get('connected') \
                    and previous.get('windows'):
                updated[provider] = {**previous, 'error': fresh.get('error', '')}
            else:
                updated[provider] = fresh
            alert = self._maybe_warn_low(provider, updated[provider])
            if alert is not None:
                alerts.append(alert)
        if 'gemini' in wanted and self.home is None:
            # Refresh cached Win32 credential timestamp in case agy refreshed an expired token in-place
            self._last_win_cred_check = -999.0
            creds, _, _ = self._scan_local_state(mono)
            self._cred_mtimes['gemini'] = creds.get('gemini', 0.0)
        self.latest = updated
        self._save_cache()
        for provider_name, label, left in alerts:
            if self.cfg.get('ai_warn_low', True):
                self.low_allowance.emit(provider_name, label, left)
        return updated

    def _run_fetch(self, force: bool, providers: set[str], now_mono: float | None = None) -> None:
        cur_force = force
        cur_providers = set(providers)
        cur_mono = now_mono
        try:
            while True:
                result = self._collect(force=cur_force, providers=cur_providers, now_mono=cur_mono)
                cur_mono = None
                if not self._stopped:
                    self.changed.emit(result)
                with self._lock:
                    if not self._stopped and (self._queued_providers or self._queued_force):
                        cur_force = self._queued_force
                        cur_providers = set(self._queued_providers) or self.active_providers()
                        self._queued_force = False
                        self._queued_providers.clear()
                        continue
                    self._busy = False
                    break
        finally:
            with self._lock:
                self._busy = False
                self._queued_providers.clear()
                self._queued_force = False
            if not self._stopped:
                self.busy_changed.emit(False)


# ---------------------------------------------------------------- drawing helpers

def health_color(left_percent: float, dark: bool) -> QColor:
    """Green while healthy, amber at <= 25% left, coral red at <= 10% left."""
    if left_percent <= 10.0:
        return QColor('#f87171' if dark else '#dc2626')
    if left_percent <= 25.0:
        return QColor('#fbbf24' if dark else '#d97706')
    return QColor('#22c55e' if dark else '#16a34a')


def paint_provider_mark(painter: QPainter, rect: QRectF, provider: str, dark: bool) -> None:
    """Vector badge for Claude, ChatGPT or Gemini at any tile or card size."""
    side = min(rect.width(), rect.height())
    box = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
    radius = side * 0.25
    cx, cy = box.center().x(), box.center().y()
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)

    if provider == 'claude':
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#d97757'))
        painter.drawRoundedRect(box, radius, radius)
        pen = QPen(QColor('#ffffff'), max(1.2, side * 0.085), Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        rays = 10
        for i in range(rays):
            angle = (i * 2 * math.pi / rays) - 0.25
            r_in = side * 0.08
            r_out = side * (0.33 if i % 2 == 0 else 0.27)
            painter.drawLine(
                QPointF(cx + math.cos(angle) * r_in, cy + math.sin(angle) * r_in),
                QPointF(cx + math.cos(angle) * r_out, cy + math.sin(angle) * r_out),
            )
    elif provider == 'chatgpt':
        bg = QColor('#26262b') if dark else QColor('#ffffff')
        edge = QColor(255, 255, 255, 34) if dark else QColor(0, 0, 0, 26)
        ink = QColor('#f4f4f5') if dark else QColor('#111114')
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(ink, max(1.15, side * 0.075), Qt.SolidLine, Qt.RoundCap))
        orbit = side * 0.145
        loop_r = side * 0.145
        for i in range(6):
            angle = i * math.pi / 3
            lx = cx + math.cos(angle) * orbit
            ly = cy + math.sin(angle) * orbit
            painter.drawEllipse(QPointF(lx, ly), loop_r, loop_r * 0.78)
    else:  # gemini
        bg = QColor('#26262b') if dark else QColor('#ffffff')
        edge = QColor(255, 255, 255, 34) if dark else QColor(0, 0, 0, 26)
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        reach = side * 0.35
        waist = side * 0.075
        star = QPainterPath()
        star.moveTo(cx, cy - reach)
        star.quadTo(cx + waist, cy - waist, cx + reach, cy)
        star.quadTo(cx + waist, cy + waist, cx, cy + reach)
        star.quadTo(cx - waist, cy + waist, cx - reach, cy)
        star.quadTo(cx - waist, cy - waist, cx, cy - reach)
        star.closeSubpath()
        grad = QLinearGradient(box.topLeft(), box.bottomRight())
        grad.setColorAt(0.0, QColor('#4285f4'))
        grad.setColorAt(0.55, QColor('#9b72cb'))
        grad.setColorAt(1.0, QColor('#d96570'))
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawPath(star)

    painter.restore()


def provider_pixmap(provider: str, size: int, dark: bool, ratio: float = 2.0) -> QPixmap:
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    paint_provider_mark(painter, QRectF(0, 0, size, size), provider, dark)
    painter.end()
    return pixmap
