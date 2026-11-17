"""Input state for the walkthrough modal operator.

Pure logic (no bpy import) so it can be unit tested in background mode.
Events are passed as plain values (``type``, ``value``, ``is_repeat``) or as
any object exposing those attributes (e.g. ``bpy.types.Event``).
"""
from __future__ import annotations

import math

# Key identifiers (bpy event.type names)
KEY_FORWARD = "W"
KEY_BACK = "S"
KEY_LEFT = "A"
KEY_RIGHT = "D"
KEYS_RUN = ("LEFT_SHIFT", "RIGHT_SHIFT")
KEY_JUMP = "SPACE"
KEY_CROUCH = "C"
KEY_PRONE = "Z"
KEY_EXIT = "ESC"
KEY_PAUSE = "P"

MOVE_KEYS = (KEY_FORWARD, KEY_BACK, KEY_LEFT, KEY_RIGHT) + KEYS_RUN

# Actions returned by InputState.handle_key()
ACTION_EXIT = "EXIT"
ACTION_PAUSE_TOGGLE = "PAUSE_TOGGLE"


class InputState:
    """Held movement keys plus edge-triggered actions.

    * W/A/S/D and both Shift keys are "held" keys (press/release).
    * Space, C, Z are edge-triggered: only the first PRESS counts; OS key
      repeat (``is_repeat``) and repeated PRESS without RELEASE are ignored.
    * Esc / P are returned as actions for the operator to handle.
    """

    def __init__(self) -> None:
        self.held: set[str] = set()
        self.jump_edge = False
        self.crouch_edge = False
        self.prone_edge = False
        # Accumulated mouse look delta in pixels since last consume.
        self.look_dx = 0.0
        self.look_dy = 0.0
        # Keys currently physically down for edge keys (to ignore repeats).
        self._edge_down: set[str] = set()

    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Forget everything (pause, focus loss, mouse leaving the region)."""
        self.held.clear()
        self._edge_down.clear()
        self.jump_edge = False
        self.crouch_edge = False
        self.prone_edge = False
        self.look_dx = 0.0
        self.look_dy = 0.0

    @property
    def is_active(self) -> bool:
        return bool(self.held or self._edge_down or self.jump_edge
                    or self.crouch_edge or self.prone_edge
                    or self.look_dx or self.look_dy)

    # ------------------------------------------------------------------
    def handle_key(self, ev_type: str, value: str, is_repeat: bool = False,
                   paused: bool = False) -> str | None:
        """Update state from a keyboard event. Returns an action or None.

        When ``paused`` only Esc / P are interpreted; movement keys are ignored.
        """
        if ev_type == KEY_EXIT:
            if value == "PRESS" and not is_repeat:
                return ACTION_EXIT
            return None
        if ev_type == KEY_PAUSE:
            if value == "PRESS" and not is_repeat:
                return ACTION_PAUSE_TOGGLE
            return None
        if paused:
            return None

        if ev_type in MOVE_KEYS:
            if value == "PRESS":
                self.held.add(ev_type)
            elif value == "RELEASE":
                self.held.discard(ev_type)
            return None

        if ev_type in (KEY_JUMP, KEY_CROUCH, KEY_PRONE):
            if value == "PRESS":
                if is_repeat or ev_type in self._edge_down:
                    return None
                self._edge_down.add(ev_type)
                if ev_type == KEY_JUMP:
                    self.jump_edge = True
                elif ev_type == KEY_CROUCH:
                    self.crouch_edge = True
                else:
                    self.prone_edge = True
            elif value == "RELEASE":
                self._edge_down.discard(ev_type)
            return None
        return None

    def handle_event(self, event, paused: bool = False) -> str | None:
        """Convenience wrapper for event-like objects."""
        return self.handle_key(event.type, event.value,
                               bool(getattr(event, "is_repeat", False)), paused)

    # ------------------------------------------------------------------
    def axes(self) -> tuple[float, float, bool]:
        """(forward, right, run) from held keys. Opposite keys cancel out.

        Normalisation of the diagonal is done by the player layer.
        """
        forward = (1.0 if KEY_FORWARD in self.held else 0.0) - (1.0 if KEY_BACK in self.held else 0.0)
        right = (1.0 if KEY_RIGHT in self.held else 0.0) - (1.0 if KEY_LEFT in self.held else 0.0)
        run = any(k in self.held for k in KEYS_RUN)
        return forward, right, run

    def consume_edges(self) -> tuple[bool, bool, bool]:
        """Return and reset (jump, crouch, prone) edges for this frame."""
        edges = (self.jump_edge, self.crouch_edge, self.prone_edge)
        self.jump_edge = self.crouch_edge = self.prone_edge = False
        return edges

    def add_look(self, dx: float, dy: float) -> None:
        self.look_dx += dx
        self.look_dy += dy

    def consume_look(self) -> tuple[float, float]:
        d = (self.look_dx, self.look_dy)
        self.look_dx = self.look_dy = 0.0
        return d


def look_angles(dx: float, dy: float, sensitivity_deg: float,
                invert_pitch: bool = False) -> tuple[float, float]:
    """Convert a pixel delta into (d_yaw, d_pitch) radians.

    Mouse right -> turn right (yaw decreases, yaw is CCW around +Z).
    Mouse up (window y grows upward in Blender) -> look up (pitch increases).
    """
    k = math.radians(sensitivity_deg)
    d_yaw = -dx * k
    d_pitch = dy * k
    if invert_pitch:
        d_pitch = -d_pitch
    return d_yaw, d_pitch


class MouseLook:
    """Relative mouse movement with cursor re-centering (warp).

    Coordinates are window-relative pixels (``event.mouse_x/y``, which is
    also what ``Window.cursor_warp`` expects).

    After a warp request, Blender delivers a synthetic MOUSEMOVE at (or very
    near) the warp target, but stale events from before the warp may arrive
    first. Each incoming position is attributed to whichever reference is
    closer (pre-warp last position vs. warp target), so neither the stale
    events nor the warp itself produce a jump.
    """

    # Give up waiting for the warp event after this many events.
    MAX_STALE_EVENTS = 8

    def __init__(self) -> None:
        self.active = False
        self.last: tuple[float, float] | None = None
        self.warp_target: tuple[float, float] | None = None
        self._stale = 0

    def reset(self) -> None:
        self.active = False
        self.last = None
        self.warp_target = None
        self._stale = 0

    def begin(self, x: float, y: float) -> None:
        """Start capturing at the current cursor position (no delta)."""
        self.active = True
        self.last = (float(x), float(y))
        self.warp_target = None
        self._stale = 0

    def motion(self, x: float, y: float) -> tuple[float, float]:
        """Feed a MOUSEMOVE position; returns the pixel delta to apply."""
        x = float(x)
        y = float(y)
        if not self.active or self.last is None:
            self.begin(x, y)
            return 0.0, 0.0
        if self.warp_target is not None:
            tx, ty = self.warp_target
            lx, ly = self.last
            d_target = (x - tx) ** 2 + (y - ty) ** 2
            d_last = (x - lx) ** 2 + (y - ly) ** 2
            if d_target <= d_last:
                # First event after the warp: delta relative to the target.
                self.warp_target = None
                self._stale = 0
                self.last = (x, y)
                return x - tx, y - ty
            self._stale += 1
            if self._stale > self.MAX_STALE_EVENTS:
                # The warp apparently never happened; stop waiting.
                self.warp_target = None
                self._stale = 0
        lx, ly = self.last
        self.last = (x, y)
        return x - lx, y - ly

    def warp_request(self, rect: tuple[int, int, int, int],
                     margin_ratio: float = 0.1,
                     edge_ratio: float = 0.15) -> tuple[int, int] | None:
        """Return a warp target (rect center) if the cursor drifted too far.

        ``rect`` = (x, y, width, height) of the region in window coordinates.
        Returns None when no warp is needed or a warp is already pending.
        A cursor near the region edge (or outside it) is always warped back,
        even while a warp is pending, so fast motion cannot leave the window.
        """
        if not self.active or self.last is None:
            return None
        rx, ry, rw, rh = rect
        if rw < 4 or rh < 4:
            return None
        cx = rx + rw // 2
        cy = ry + rh // 2
        lx, ly = self.last
        if self.warp_target is not None:
            bx = rw * edge_ratio
            by = rh * edge_ratio
            near_edge = (lx < rx + bx or lx >= rx + rw - bx
                         or ly < ry + by or ly >= ry + rh - by)
            if not near_edge:
                return None
            self.warp_target = (float(cx), float(cy))
            self._stale = 0
            return cx, cy
        limit = max(2.0, min(rw, rh) * margin_ratio)
        if abs(lx - cx) > limit or abs(ly - cy) > limit:
            self.warp_target = (float(cx), float(cy))
            self._stale = 0
            return cx, cy
        return None


def point_in_rect(x: float, y: float, rect: tuple[int, int, int, int]) -> bool:
    """Half-open hit test; ``rect`` = (x, y, width, height)."""
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def classify_point(x: float, y: float, window_rect, passthrough_rects) -> str:
    """Decide how an event at window position (x, y) is routed.

    Returns ``"PASS"`` when over a pass-through UI region (sidebar), ``"OWN"``
    when inside the simulation WINDOW region, otherwise ``"OUTSIDE"``.
    Pass-through regions are tested first because with region overlap the
    WINDOW region extends underneath the sidebar.
    """
    for r in passthrough_rects:
        if point_in_rect(x, y, r):
            return "PASS"
    if window_rect is not None and point_in_rect(x, y, window_rect):
        return "OWN"
    return "OUTSIDE"
