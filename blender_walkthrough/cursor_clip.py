"""Confine the OS cursor to the active Blender window while looking around.

Windows only (``user32.ClipCursor``); a no-op on other platforms. The clip is
applied to the client area of the foreground window, which is the dedicated
walkthrough window whenever it is receiving mouse-look events. Windows may
drop the clip on focus changes, so callers re-apply it on mouse events.
"""
from __future__ import annotations

import sys

_clipped = False

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_POINT)]
    _user32.ClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]

    def _foreground_client_rect():
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != _kernel32.GetCurrentProcessId():
            return None   # another application is in front: never clip
        rc = wintypes.RECT()
        if not _user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        tl = _POINT(rc.left, rc.top)
        br = _POINT(rc.right, rc.bottom)
        if not (_user32.ClientToScreen(hwnd, ctypes.byref(tl))
                and _user32.ClientToScreen(hwnd, ctypes.byref(br))):
            return None
        if br.x - tl.x < 8 or br.y - tl.y < 8:
            return None
        return wintypes.RECT(tl.x, tl.y, br.x, br.y)

    def confine() -> bool:
        """Clip the cursor to the foreground Blender window. Returns success."""
        global _clipped
        try:
            rect = _foreground_client_rect()
            if rect is None:
                return False
            if _user32.ClipCursor(ctypes.byref(rect)):
                _clipped = True
                return True
        except Exception:
            pass
        return False

    def release() -> None:
        """Remove the clip (idempotent)."""
        global _clipped
        if not _clipped:
            return
        try:
            _user32.ClipCursor(None)
        except Exception:
            pass
        _clipped = False

else:
    def confine() -> bool:
        return False

    def release() -> None:
        pass
