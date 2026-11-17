"""Pressing the taskbar's "Show Hidden Icons" button through UI Automation (COM, called with ctypes),
so the Tray widget opens Windows' own panel of background apps: their icons, menus and all."""

from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes

from . import win

ole32 = ctypes.WinDLL('ole32')
oleaut32 = ctypes.WinDLL('oleaut32')
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.HRESULT
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]


def _guid(text: str):
    return (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)


CLSID_CUIAutomation = _guid('ff48dba4-60ef-4201-aa87-54103eef594e')
IID_IUIAutomation = _guid('30cbe57d-d9d0-452a-ab13-7ac5ac4825ee')
IID_IUIAutomationInvokePattern = _guid('fb377fbe-8ea6-46d5-9c73-6499642d3059')
UIA_InvokePatternId = 10000
TreeScope_Descendants = 4

# vtable slots (UIAutomationClient.h)
AUTOMATION_ELEMENT_FROM_HANDLE, AUTOMATION_TRUE_CONDITION = 6, 21
ELEMENT_FIND_ALL, ELEMENT_PATTERN_AS, ELEMENT_NAME, ELEMENT_CLASS = 6, 14, 23, 30
ARRAY_LENGTH, ARRAY_ELEMENT = 3, 4
INVOKE = 3

# The button's name in the languages this PC uses (Windows names it in its own display language);
# failing those, the first plain tray button that is not a known one is taken.
NAMES = ('show hidden icons', '显示隐藏的图标')


def _call(obj, index, *args, argtypes=()):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(table[index])(obj, *args)


def _release(obj) -> None:
    if obj:
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])(obj)


def _text(element, index) -> str:
    value = ctypes.c_void_p()
    _call(element, index, ctypes.byref(value), argtypes=(ctypes.c_void_p,))
    try:
        return ctypes.wstring_at(value.value) if value.value else ''
    finally:
        oleaut32.SysFreeString(value)


def _tray_buttons() -> tuple[ctypes.c_void_p, list[tuple[ctypes.c_void_p, str, str]]] | None:
    """The taskbar's notification-area buttons: (automation, [(element, name, class)]) to release."""
    tray = win.user32.FindWindowW('Shell_TrayWnd', None)
    if not tray:
        return None
    ole32.CoInitializeEx(None, 0x2)
    automation = ctypes.c_void_p()
    if ole32.CoCreateInstance(CLSID_CUIAutomation, None, 1, IID_IUIAutomation, ctypes.byref(automation)) != 0:
        return None
    root = ctypes.c_void_p()
    condition = ctypes.c_void_p()
    found = ctypes.c_void_p()
    buttons = []
    try:
        _call(automation, AUTOMATION_ELEMENT_FROM_HANDLE, ctypes.c_void_p(tray), ctypes.byref(root),
              argtypes=(ctypes.c_void_p, ctypes.c_void_p))
        _call(automation, AUTOMATION_TRUE_CONDITION, ctypes.byref(condition), argtypes=(ctypes.c_void_p,))
        _call(root, ELEMENT_FIND_ALL, TreeScope_Descendants, condition, ctypes.byref(found),
              argtypes=(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p))
        count = ctypes.c_int()
        _call(found, ARRAY_LENGTH, ctypes.byref(count), argtypes=(ctypes.c_void_p,))
        for index in range(count.value):
            element = ctypes.c_void_p()
            _call(found, ARRAY_ELEMENT, index, ctypes.byref(element), argtypes=(ctypes.c_int, ctypes.c_void_p))
            cls = _text(element, ELEMENT_CLASS)
            if cls.startswith('SystemTray.'):
                buttons.append((element, _text(element, ELEMENT_NAME), cls))
            else:
                _release(element)
    except OSError:
        pass
    finally:
        for obj in (found, condition, root):
            _release(obj)
    return automation, buttons


def hidden_icons_button_name() -> str | None:
    """The name of the button that would be pressed (for checking, without pressing it)."""
    got = _tray_buttons()
    if got is None:
        return None
    automation, buttons = got
    try:
        chosen = _choose(buttons)
        return chosen[1] if chosen else None
    finally:
        for element, _, _ in buttons:
            _release(element)
        _release(automation)


def _choose(buttons):
    """The hidden-icons button. Windows has changed its name before: on build 26300 (2026-11-17) it is
    'Show Hidden Icons Hide' - the name and then its state - so a name that starts with, or holds, one
    of NAMES (or just 'hidden icons' / '隐藏的图标') is taken, never one only equal to it."""
    for button in buttons:
        name = ' '.join(button[1].split()).lower()
        if any(name == known or name.startswith(known + ' ') for known in NAMES):
            return button
    for button in buttons:
        name = button[1].lower()
        if 'hidden icons' in name or '隐藏的图标' in name:
            return button
    return None


def open_hidden_icons() -> bool:
    """Press "Show Hidden Icons": Windows opens the panel of the notification area's other icons."""
    got = _tray_buttons()
    if got is None:
        return False
    automation, buttons = got
    try:
        chosen = _choose(buttons)
        if chosen is None:
            return False
        pattern = ctypes.c_void_p()
        _call(chosen[0], ELEMENT_PATTERN_AS, UIA_InvokePatternId, IID_IUIAutomationInvokePattern,
              ctypes.byref(pattern), argtypes=(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p))
        if not pattern:
            return False
        try:
            _call(pattern, INVOKE)
            return True
        finally:
            _release(pattern)
    except OSError:
        return False
    finally:
        for element, _, _ in buttons:
            _release(element)
        _release(automation)
