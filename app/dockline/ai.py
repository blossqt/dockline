"""AI plan allowances (Claude, ChatGPT, Gemini), read locally and from each vendor's own usage endpoint.

Reads the sign-ins already kept on this PC by Claude Code (~/.claude/.credentials.json), OpenAI Codex
(~/.codex/auth.json and ~/.codex/sessions) and Gemini CLI / Antigravity CLI (~/.gemini/oauth_creds.json
and Windows Credential Manager). Read-only: credential stores are never written by Dockline, and tokens
are never saved in Dockline's settings or cache.
"""

from __future__ import annotations

import ctypes
import datetime as dt
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

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
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
        'hint': 'Sign in with Gemini CLI on this PC to show your Pro and Flash quotas.',
    },
}

CLAUDE_USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
CHATGPT_USAGE_URL = 'https://chatgpt.com/backend-api/wham/usage'
GEMINI_LOAD_URL = 'https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist'
GEMINI_QUOTA_URL = 'https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota'
ANTIGRAVITY_LOAD_URL = 'https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist'
ANTIGRAVITY_QUOTA_URL = 'https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota'

POLL_EVERY = 300        # seconds between background network refreshes
WATCH_EVERY = 15        # seconds between local credential/session mtime checks
RATE_LIMIT_WAIT = 600   # seconds to back off if a provider answers HTTP 429
LOW_THRESHOLD = 15.0    # warn once per reset window when remaining allowance drops to or below 15%
CREATE_NO_WINDOW = 0x08000000

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
    pass


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


def format_age(updated_at: float | None, now: float | None = None) -> str:
    if updated_at is None:
        return ''
    current = time.time() if now is None else now
    age = max(0.0, current - updated_at)
    if age < 45:
        return 'Updated just now'
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
    req_headers = {'Accept': 'application/json', **(headers or {})}
    if payload is not None:
        req_headers.setdefault('Content-Type', 'application/json')
    request = urllib.request.Request(url, data=payload, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8', errors='replace')
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        if err.code == 429:
            raise _RateLimited() from None
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


def latest_codex_session_limits(sessions_dir: Path) -> tuple[list[dict], str, float | None]:
    """Read the newest token_count rate_limits from local Codex rollout logs without network traffic."""
    if not sessions_dir.is_dir():
        return [], '', None
    try:
        files = sorted(sessions_dir.rglob('*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)[:4]
    except OSError:
        return [], '', None
    for file in files:
        try:
            mtime = file.stat().st_mtime
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

def fetch_claude(home: Path | None = None, opener=_http_json) -> dict:
    path = claude_credentials_path(home)
    if not path.is_file():
        return _empty_state(False)
    try:
        raw = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return _empty_state(False, 'Could not read Claude credentials.')
    oauth = raw.get('claudeAiOauth') if isinstance(raw, dict) else None
    token = (oauth.get('accessToken') if isinstance(oauth, dict) else None) or ''
    if not token:
        return _empty_state(False)
    plan_raw = str((oauth or {}).get('subscriptionType') or '').strip()
    plan = plan_raw.capitalize() if plan_raw else ''
    try:
        payload = opener(
            CLAUDE_USAGE_URL,
            headers={
                'Authorization': f'Bearer {token}',
                'anthropic-beta': 'oauth-2025-04-20',
                'User-Agent': 'claude-code/2.1.0',
            },
        )
    except _RateLimited:
        raise
    except _HttpStatus as err:
        if err.code in (401, 403):
            return _empty_state(True, 'Claude session expired — open Claude Code to refresh your sign-in.')
        return _empty_state(True, f'Claude usage unavailable ({err}).')
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


def _query_gemini_with_token(token: str, source: str = '', opener=_http_json) -> tuple[list[dict], str]:
    is_agy = 'antigravity' in source.lower()
    endpoints = (
        ((ANTIGRAVITY_LOAD_URL, ANTIGRAVITY_QUOTA_URL, 'antigravity/1.0', 'ANTIGRAVITY', 'Antigravity'),
         (GEMINI_LOAD_URL, GEMINI_QUOTA_URL, 'antigravity/1.0', 'IDE_UNSPECIFIED', 'Antigravity'))
        if is_agy else
        ((GEMINI_LOAD_URL, GEMINI_QUOTA_URL, 'GeminiCLI/0.1.0', 'IDE_UNSPECIFIED', ''),
         (GEMINI_LOAD_URL, GEMINI_QUOTA_URL, 'antigravity/1.0', 'IDE_UNSPECIFIED', ''))
    )
    last_http: _HttpStatus | None = None

    for load_url, quota_url, ua, ide_type, plan_label in endpoints:
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

        for body in ([{'project': project}, {}] if project else [{}]):
            try:
                payload = opener(quota_url, method='POST', headers=headers, body=body)
                windows = parse_gemini_quota(payload, antigravity=is_agy)
                if windows:
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
            'plan': '',
            'windows': [
                {'key': 'pro', 'label': 'Pro', 'used': 18.0, 'resets_at': base + 13 * 3600 + 50 * 60},
                {'key': 'flash', 'label': 'Flash', 'used': 6.0, 'resets_at': base + 13 * 3600 + 50 * 60},
            ],
            'updated_at': base,
            'error': '',
        },
    }


# ---------------------------------------------------------------- background coordinator

class AIUsage(QObject):
    changed = Signal(dict)
    low_allowance = Signal(str, str, float)     # provider name, window label, remaining %

    def __init__(self, cfg, testing: bool = False, home: Path | None = None, opener=_http_json):
        super().__init__()
        self.cfg = cfg
        self.testing = testing
        self.home = home
        self.opener = opener
        self._busy = False
        self._last_poll = 0.0
        self._backoff_until: dict[str, float] = {}
        self._mtimes: dict[str, float] = {}
        self._warned: set[tuple[str, str, int]] = set()
        self.latest: dict[str, dict] = {p: _empty_state(False) for p in PROVIDERS}
        if testing:
            self.latest = sample_snapshot()
        else:
            self._load_cache()
            self._mtimes = self._current_mtimes()

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

    def _current_mtimes(self) -> dict[str, float]:
        found: dict[str, float] = {}
        for key, path in (
            ('claude', claude_credentials_path(self.home)),
            ('chatgpt', codex_home_path(self.home) / 'auth.json'),
            ('gemini', gemini_credentials_path(self.home)),
        ):
            try:
                if path.is_file():
                    found[key] = path.stat().st_mtime
            except OSError:
                pass
        if self.home is None:
            for target, _tok, _exp, written in read_windows_gemini_credentials():
                if written:
                    found[f'cred:{target}'] = written
        sessions = codex_home_path(self.home) / 'sessions'
        try:
            if sessions.is_dir():
                newest = max((p.stat().st_mtime for p in sessions.rglob('*.jsonl')), default=0.0)
                if newest:
                    found['codex_sessions'] = newest
        except OSError:
            pass
        return found

    def watch_files(self) -> None:
        """Notice a fresh login or token update promptly without polling endpoints every few seconds."""
        if self.testing or self._busy:
            return
        now_mtimes = self._current_mtimes()
        if now_mtimes != self._mtimes:
            self._mtimes = now_mtimes
            if time.monotonic() - self._last_poll >= 15.0:
                self.fetch()

    def fetch(self, force: bool = False) -> None:
        if self.testing or self._busy:
            return
        self._busy = True
        self._last_poll = time.monotonic()
        threading.Thread(target=self._run_fetch, args=(force,), daemon=True).start()

    def refresh_sync(self, force: bool = True) -> dict[str, dict]:
        """Synchronous refresh helper for deterministic unit tests."""
        self._last_poll = time.monotonic()
        return self._collect(force=force)

    def _collect(self, force: bool = False) -> dict[str, dict]:
        mono = time.monotonic()
        updated = dict(self.latest)
        alerts: list[tuple[str, str, float]] = []
        enabled = set(self.cfg.get('ai_enabled') or PROVIDERS)
        individual = set(self.cfg.get('widgets') or ())
        active = enabled | (individual & set(PROVIDERS))
        for provider in PROVIDERS:
            if provider not in active:
                continue
            if not force and mono < self._backoff_until.get(provider, 0.0):
                continue
            previous = self.latest.get(provider) or _empty_state(False)
            try:
                fresh = FETCHERS[provider](home=self.home, opener=self.opener)
            except _RateLimited:
                self._backoff_until[provider] = mono + RATE_LIMIT_WAIT
                if previous.get('connected') and previous.get('windows'):
                    updated[provider] = {**previous, 'error': ''}
                else:
                    updated[provider] = _empty_state(True, 'Rate-limited — will retry shortly.')
                continue
            if not fresh.get('connected') and fresh.get('signed_in') and previous.get('connected') \
                    and previous.get('windows'):
                updated[provider] = {**previous, 'error': fresh.get('error', '')}
            else:
                updated[provider] = fresh
            state = updated[provider]
            if state.get('connected') and state.get('windows'):
                first = state['windows'][0]
                left = remaining_pct(first)
                token_key = (provider, first['key'], round(first.get('resets_at') or 0))
                if left <= LOW_THRESHOLD and token_key not in self._warned:
                    self._warned.add(token_key)
                    alerts.append((INFO[provider]['name'], first['label'], left))
        self.latest = updated
        self._save_cache()
        for provider_name, label, left in alerts:
            if self.cfg.get('ai_warn_low', True):
                self.low_allowance.emit(provider_name, label, left)
        return updated

    def _run_fetch(self, force: bool) -> None:
        try:
            result = self._collect(force=force)
            self.changed.emit(result)
        finally:
            self._busy = False


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
