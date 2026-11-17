"""Dedicated simulation window: creation, identification, validation, closing.

Windows/areas/regions are identified by ``as_pointer()`` and re-resolved from
``window_manager.windows`` whenever they are needed, so stale Python
references are never dereferenced after the user closes a window.
"""
from __future__ import annotations

import bpy


class WindowError(Exception):
    """The dedicated window could not be created or configured."""


# ----------------------------------------------------------------------
# Lookup helpers
# ----------------------------------------------------------------------
def find_window(wm, window_ptr):
    if wm is None or not window_ptr:
        return None
    for win in wm.windows:
        if win.as_pointer() == window_ptr:
            return win
    return None


def find_area(window, area_ptr):
    if window is None or not area_ptr:
        return None
    screen = window.screen
    if screen is None:
        return None
    for area in screen.areas:
        if area.as_pointer() == area_ptr:
            return area
    return None


def find_region(area, region_ptr):
    if area is None or not region_ptr:
        return None
    for region in area.regions:
        if region.as_pointer() == region_ptr:
            return region
    return None


def largest_window_region(area):
    best = None
    for region in area.regions:
        if region.type == "WINDOW":
            if best is None or region.width * region.height > best.width * best.height:
                best = region
    return best


def largest_area(screen, area_type=None):
    best = None
    for area in screen.areas:
        if area_type is not None and area.type != area_type:
            continue
        if best is None or area.width * area.height > best.width * best.height:
            best = area
    return best


class AreaRef:
    """Pointer-based identity of a (window, area, region) triple."""

    def __init__(self, window=None, area=None, region=None) -> None:
        self.window_ptr = window.as_pointer() if window is not None else 0
        self.area_ptr = area.as_pointer() if area is not None else 0
        self.region_ptr = region.as_pointer() if region is not None else 0

    def resolve(self, wm):
        """Return (window, area, region) or None if any part is gone."""
        win = find_window(wm, self.window_ptr)
        area = find_area(win, self.area_ptr)
        if area is None or area.type != "VIEW_3D":
            return None
        region = find_region(area, self.region_ptr)
        if region is None:
            return None
        return win, area, region

    def window(self, wm):
        return find_window(wm, self.window_ptr)


def space_and_rv3d(area, region=None):
    space = area.spaces.active if area is not None else None
    if space is None or space.type != "VIEW_3D":
        return None, None
    rv3d = None
    if region is not None and region.type == "WINDOW":
        rv3d = getattr(region, "data", None)
    if rv3d is None:
        rv3d = space.region_3d
    return space, rv3d


# ----------------------------------------------------------------------
# Creation
# ----------------------------------------------------------------------
def create_dedicated_window(context):
    """Open a new Blender window and return it.

    The new window is identified by diffing window pointers before/after
    ``wm.window_new``. Raises WindowError on any failure; the caller's
    window is never repurposed.
    """
    if bpy.app.background:
        raise WindowError("A dedicated window cannot be opened in background mode")
    wm = context.window_manager
    before = {w.as_pointer() for w in wm.windows}
    try:
        result = bpy.ops.wm.window_new()
    except Exception as exc:  # poll failure or platform issue
        raise WindowError(f"Could not open a new window: {exc}") from exc
    if "FINISHED" not in result:
        raise WindowError(f"Could not open a new window ({', '.join(result)})")
    new = [w for w in wm.windows if w.as_pointer() not in before]
    if len(new) != 1:
        # Unexpected; close anything we may have created and give up.
        for w in new:
            schedule_close(wm, w.as_pointer())
        raise WindowError("Could not identify the new window")
    return new[0]


def configure_space(space) -> None:
    """Hide everything except the sidebar (needed for Pause/Stop buttons)."""
    for ident, value in (("show_region_header", False),
                         ("show_region_tool_header", False),
                         ("show_region_toolbar", False),
                         ("show_region_hud", False),
                         ("show_region_asset_shelf", False),
                         ("show_region_ui", True),
                         ("show_gizmo", False)):
        if hasattr(space, ident):
            try:
                setattr(space, ident, value)
            except Exception:
                pass


def prepare_area(context, window):
    """Make ``window`` contain one VIEW_3D area to own. Returns (area, region).

    Region visibility is configured separately by :func:`configure_space`
    (after the caller has snapshotted the area).

    * Picks the largest VIEW_3D area, or converts the largest area to VIEW_3D.
    * Tries to maximise it (``screen.screen_full_area``) so the dedicated
      window has no other editors; falls back to the multi-area layout.
    """
    screen = window.screen
    if screen is None or len(screen.areas) == 0:
        raise WindowError("The new window has no screen")
    area = largest_area(screen, "VIEW_3D")
    if area is None:
        area = largest_area(screen)
        try:
            area.type = "VIEW_3D"
        except Exception as exc:
            raise WindowError(f"Could not create a 3D View in the new window: {exc}") from exc
    if len(screen.areas) > 1:
        region = largest_window_region(area)
        try:
            with context.temp_override(window=window, screen=screen, area=area, region=region):
                bpy.ops.screen.screen_full_area(use_hide_panels=False)
        except Exception:
            pass  # keep the multi-area layout; only our area is captured
        screen = window.screen
        maximised = largest_area(screen, "VIEW_3D")
        if maximised is not None:
            area = maximised
    if area.type != "VIEW_3D":
        raise WindowError("Dedicated area is not a 3D View")
    region = largest_window_region(area)
    if region is None:
        raise WindowError("Dedicated 3D View has no main region")
    return area, region


def select_sidebar_tab(area, category="Walkthrough") -> None:
    """Show our sidebar tab (best-effort; property may be read-only)."""
    for r in area.regions:
        if r.type == "UI":
            try:
                r.active_panel_category = category
            except Exception:
                pass


# ----------------------------------------------------------------------
# Event routing geometry
# ----------------------------------------------------------------------
# Region types whose events are passed through to Blender's UI, mapped to the
# SpaceView3D flag that tells whether the region is shown.
_PASSTHROUGH_REGIONS = {"UI": "show_region_ui", "HEADER": "show_region_header"}


def region_rect(region):
    return (region.x, region.y, region.width, region.height)


def routing_rects(area, region):
    """(window_rect, [passthrough rects]) in window coordinates."""
    space = area.spaces.active
    passthrough = []
    for r in area.regions:
        flag = _PASSTHROUGH_REGIONS.get(r.type)
        if flag is None:
            continue
        if not getattr(space, flag, True):
            continue
        if r.width > 1 and r.height > 1:
            passthrough.append(region_rect(r))
    return region_rect(region), passthrough


# ----------------------------------------------------------------------
# Closing
# ----------------------------------------------------------------------
def close_window_now(wm, window_ptr) -> bool:
    win = find_window(wm, window_ptr)
    if win is None:
        return False
    # Never close the last window (would quit Blender).
    if len(wm.windows) <= 1:
        return False
    try:
        with bpy.context.temp_override(window=win, screen=win.screen):
            bpy.ops.wm.window_close()
        return True
    except Exception as exc:
        print(f"[Walkthrough] Could not close window: {exc}")
        return False


def schedule_close(wm, window_ptr) -> None:
    """Close the window from a timer, outside of any event handler.

    Closing a window from inside its own modal handler would free the
    handler while it executes, so it is always deferred.
    """
    if not window_ptr:
        return

    def _close():
        try:
            close_window_now(bpy.context.window_manager, window_ptr)
        except Exception as exc:
            print(f"[Walkthrough] Deferred window close failed: {exc}")
        return None

    bpy.app.timers.register(_close, first_interval=0.05)
