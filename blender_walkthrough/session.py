"""Walkthrough session manager and state machine (``session.SESSION``).

States::

    IDLE -> PREPARING -> RUNNING <-> PAUSED -> REBUILDING -> PAUSED
    RUNNING/PAUSED -> STOPPING -> IDLE
    (any failure)  -> ERROR -> STOPPING -> IDLE

All cleanup (timer, cursor, view restore, collision data) is concentrated in
:meth:`SessionManager.stop`, which is idempotent and never raises.
"""
from __future__ import annotations

import math
import time
import traceback

import bpy
from mathutils import Vector

from . import cursor_clip, units, viewport, windowing
from .diagnostics import Diagnostics
from .input import (ACTION_EXIT, ACTION_PAUSE_TOGGLE, InputState, MouseLook,
                    classify_point, look_angles)

IDLE = "IDLE"
PREPARING = "PREPARING"
RUNNING = "RUNNING"
PAUSED = "PAUSED"
REBUILDING = "REBUILDING"
STOPPING = "STOPPING"
ERROR = "ERROR"

ACTIVE_STATES = {RUNNING, PAUSED, REBUILDING}

TIMER_STEP = 1.0 / 60.0          # modal TIMER interval (physics uses its own fixed dt)
WATCHDOG_INTERVAL = 0.25         # seconds, detects closed windows / scene switches

# Real-world constants (meters) converted to world units at start.
SKIN_M = 0.002
VIEW_DISTANCE_M = 0.05           # tiny orbit distance behind the eye
MAX_CLIP_START_M = 0.05          # near clip must stay well below the capsule radius
GROUND_PROBE_UP_M = 0.05

MOUSE_EVENTS = {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}


class StartError(Exception):
    """Start refused (validation failure). Message is user facing."""


class SessionManager:
    def __init__(self) -> None:
        self.state = IDLE
        self.diagnostics = Diagnostics()
        self.token = 0
        self._reset_runtime()

    # ------------------------------------------------------------------
    # Runtime fields
    # ------------------------------------------------------------------
    def _reset_runtime(self) -> None:
        self.world = None
        self.solver = None
        self.player = None
        self.dedicated: windowing.AreaRef | None = None
        self.source: windowing.AreaRef | None = None
        self.dedicated_snapshot: viewport.ViewStateSnapshot | None = None
        self.timer = None
        self.scene_ptr = 0
        self.view_layer_name = ""
        self.close_window_on_exit = True
        self.mouse_sensitivity = 0.05
        self.invert_pitch = False
        self.view_distance = VIEW_DISTANCE_M
        self.input = InputState()
        self.mouse = MouseLook()
        self.cursor_hidden = False
        self.last_time = 0.0

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    def _set_state(self, state: str) -> None:
        self.state = state
        self.diagnostics.state = state

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------
    def start(self, context) -> tuple[bool, str, list[str]]:
        """Start a session from a VIEW_3D context. Returns (ok, message, warnings)."""
        if self.state != IDLE:
            return False, f"A walkthrough session is already {self.state.lower()}", []
        warnings: list[str] = []
        self.diagnostics = Diagnostics()
        self._reset_runtime()
        self._set_state(PREPARING)
        try:
            self._start(context, warnings)
        except Exception as exc:
            if not isinstance(exc, StartError):
                traceback.print_exc()
            msg = str(exc) or type(exc).__name__
            self.fail(msg)
            return False, msg, warnings
        return True, "Walkthrough started (Esc: exit, P: pause)", warnings

    def _start(self, context, warnings: list[str]) -> None:
        # Lazy imports: the physics/player modules are owned by other modules
        # and must not be required just to register the add-on.
        if bpy.app.background:
            raise StartError("Walkthrough needs the Blender UI (not available in background mode)")
        from . import collision_world, physics, player as player_mod
        wm = context.window_manager
        scene = context.scene
        view_layer = context.view_layer
        src_window = context.window
        src_area = context.area
        if src_window is None or src_area is None or src_area.type != "VIEW_3D":
            raise StartError("Start the walkthrough from a 3D View")
        src_region = context.region
        if src_region is None or src_region.type != "WINDOW":
            src_region = windowing.largest_window_region(src_area)
        src_space, src_rv3d = windowing.space_and_rv3d(src_area, src_region)
        if src_space is None or src_rv3d is None:
            raise StartError("The source 3D View has no view region")

        settings = scene.walkthrough_settings
        # --- validation: cursor and units ---------------------------------
        foot = scene.cursor.location.copy()
        if not all(math.isfinite(c) for c in foot):
            raise StartError("3D cursor location is invalid")
        _wpm, unit_warning = units.world_per_meter(scene)
        if unit_warning:
            warnings.append(unit_warning)

        def w(m):  # meters -> world units
            return units.meters_to_world(m, scene)

        self.scene_ptr = scene.as_pointer()
        self.view_layer_name = view_layer.name
        self.close_window_on_exit = bool(settings.close_window_on_exit)
        self.mouse_sensitivity = float(settings.mouse_sensitivity)
        self.invert_pitch = bool(settings.invert_pitch)
        self.view_distance = w(VIEW_DISTANCE_M)

        # --- collision snapshot (before opening anything) ----------------
        depsgraph = context.evaluated_depsgraph_get()
        self.world = collision_world.CollisionWorld.build(
            context, view_layer=view_layer, depsgraph=depsgraph)
        self.diagnostics.update_from_world(self.world)
        if self.world.stats.get("object_count", 0) == 0:
            warnings.append("No visible mesh objects: nothing to collide with")

        # --- player -------------------------------------------------------
        defaults = player_mod.PlayerSettings(eye_height=1.6, walk_speed=3.0, run_speed=5.0)
        p_settings = player_mod.PlayerSettings(
            eye_height=w(settings.eye_height),
            walk_speed=w(settings.walk_speed),
            run_speed=w(settings.run_speed),
            jump_height=w(defaults.jump_height),
            gravity=w(defaults.gravity),
            radius=w(defaults.radius),
            head_clearance=w(settings.head_clearance),
            step_height=w(defaults.step_height),
            max_slope_deg=defaults.max_slope_deg,
        )
        config = physics.PhysicsConfig(
            radius=p_settings.radius,
            step_height=p_settings.step_height,
            max_slope_deg=p_settings.max_slope_deg,
            skin=w(SKIN_M),
        )
        self.solver = physics.PhysicsSolver(self.world, config)
        start_rot = viewport.view_forward_rotation(src_space, src_rv3d, scene)
        yaw, pitch = player_mod.PlayerController.yaw_pitch_from_rotation(start_rot)
        self.player = player_mod.PlayerController(self.solver, p_settings, foot.copy(), yaw, pitch)

        # Refuse to start embedded in geometry (touching the floor is fine).
        if not self.solver.is_space_free(foot, self.player.capsule_height()):
            raise StartError("Start position is inside geometry: move the 3D cursor to free space")
        up = w(GROUND_PROBE_UP_M)
        hit = self.world.ray_cast(foot + Vector((0.0, 0.0, up)), Vector((0.0, 0.0, -1.0)),
                                  up + p_settings.step_height)
        if hit is None:
            warnings.append("No ground under the 3D cursor: the player will fall")

        # --- dedicated window ----------------------------------------------
        # The source window is only read (shading/overlay/view rotation are
        # copied from it); it is never modified, so it needs no restore.
        self.source = windowing.AreaRef(src_window, src_area, src_region)
        win = windowing.create_dedicated_window(context)
        # Remember the window immediately so a later failure closes it.
        self.dedicated = windowing.AreaRef(win)
        if win.screen is None or win.screen.as_pointer() == src_window.screen.as_pointer():
            raise StartError("The new window shares the editing screen; refusing to modify it")
        area, region = windowing.prepare_area(context, win)
        if area.as_pointer() == src_area.as_pointer():
            raise StartError("Could not get a separate 3D View for the walkthrough")
        self.dedicated = windowing.AreaRef(win, area, region)
        space, rv3d = windowing.space_and_rv3d(area, region)
        if space is None or rv3d is None:
            raise StartError("The dedicated 3D View has no view region")
        viewport.copy_display_settings(src_space, space)
        # Snapshot of the dedicated area as copied from the source, used to
        # restore it if the window is kept open after the session.
        self.dedicated_snapshot = viewport.ViewStateSnapshot.capture(space, rv3d)
        windowing.configure_space(space)
        windowing.select_sidebar_tab(area)
        if space.clip_start > w(MAX_CLIP_START_M):
            space.clip_start = w(MAX_CLIP_START_M)
        try:
            rv3d.lock_rotation = False
            rv3d.use_box_clip = False
        except Exception:
            pass
        self._apply_view(area, region)

        # --- modal operator in the dedicated window -----------------------
        self.token += 1
        with context.temp_override(window=win, screen=win.screen, area=area, region=region):
            result = bpy.ops.walkthrough.modal("INVOKE_DEFAULT")
        if "RUNNING_MODAL" not in result:
            raise StartError("Could not start input handling in the walkthrough window")
        self.timer = wm.event_timer_add(TIMER_STEP, window=win)
        self.last_time = time.perf_counter()
        self._set_state(RUNNING)
        self.diagnostics.warnings = list(self.diagnostics.warnings) + warnings
        _watchdog_start()

    # ------------------------------------------------------------------
    # Pause / resume / rebuild
    # ------------------------------------------------------------------
    def pause(self) -> bool:
        if self.state != RUNNING:
            return False
        self._set_state(PAUSED)
        self.input.clear()
        self._release_mouse()
        return True

    def resume(self) -> bool:
        if self.state != PAUSED:
            return False
        self.input.clear()
        self.mouse.reset()
        self.last_time = time.perf_counter()   # no dt spike after the pause
        self._set_state(RUNNING)
        return True

    def rebuild(self, context) -> tuple[bool, str]:
        """Rebuild collision data (PAUSED only). Old world is kept on failure."""
        if self.state != PAUSED:
            return False, "Pause the walkthrough before rebuilding collisions"
        from . import collision_world
        self._set_state(REBUILDING)
        try:
            scene = self._scene()
            view_layer = scene.view_layers.get(self.view_layer_name) if scene else None
            if view_layer is None:
                raise RuntimeError("Simulation view layer is gone")
            if context.view_layer is not None and context.view_layer.as_pointer() == view_layer.as_pointer():
                depsgraph = context.evaluated_depsgraph_get()
            else:
                depsgraph = view_layer.depsgraph
            new_world = collision_world.CollisionWorld.build(
                context, view_layer=view_layer, depsgraph=depsgraph)
        except Exception as exc:
            msg = f"Rebuild failed: {exc}"
            self.diagnostics.set_error(msg)
            self._set_state(PAUSED)
            return False, msg
        old = self.world
        self.solver.world = new_world
        self.world = new_world
        try:
            if old is not None:
                old.free()
        except Exception:
            pass
        self.diagnostics.update_from_world(new_world)
        self.diagnostics.last_error = ""
        msg = (f"Collisions rebuilt: {new_world.stats.get('object_count', 0)} objects, "
               f"{new_world.stats.get('triangle_count', 0)} triangles")
        try:
            if not self.solver.is_space_free(self.player.foot, self.player.capsule_height()):
                warn = "Player overlaps the rebuilt geometry"
                self.diagnostics.warnings.append(warn)
                msg += f" (warning: {warn})"
        except Exception:
            pass
        self._set_state(PAUSED)
        return True, msg

    # ------------------------------------------------------------------
    # Stop (idempotent, never raises)
    # ------------------------------------------------------------------
    def fail(self, msg: str) -> None:
        """Record an error and stop safely."""
        self.diagnostics.set_error(msg)
        print(f"[Walkthrough] Error: {msg}")
        if self.state in (IDLE, STOPPING):
            return
        # A window opened by a failed start is always closed.
        close = True if self.state == PREPARING else None
        self._set_state(ERROR)
        self.stop(reason=msg, close_window=close)

    def stop(self, reason: str = "", close_window: bool | None = None) -> None:
        if self.state in (IDLE, STOPPING):
            return
        self._set_state(STOPPING)
        if close_window is None:
            close_window = self.close_window_on_exit
        wm = None
        win_ptr = 0
        try:
            wm = bpy.context.window_manager
            win = self.dedicated.window(wm) if self.dedicated else None
            win_ptr = win.as_pointer() if win is not None else 0
            # Timer: when the window is already gone Blender has freed it.
            if self.timer is not None and win is not None:
                try:
                    wm.event_timer_remove(self.timer)
                except Exception:
                    pass
            self.timer = None
            self.input.clear()
            cursor_clip.release()   # even if the window is already gone
            if win is not None:
                self._release_mouse(win)
            # Restore the dedicated view if it stays open. The source
            # window is never modified, so nothing there is overwritten
            # (user navigation during the session is preserved).
            if not close_window and self.dedicated is not None:
                resolved = self.dedicated.resolve(wm)
                if resolved is not None and self.dedicated_snapshot is not None:
                    _w, area, region = resolved
                    space, rv3d = windowing.space_and_rv3d(area, region)
                    self.dedicated_snapshot.restore(space, rv3d)
                    area.tag_redraw()
            if self.world is not None:
                try:
                    self.world.free()
                except Exception:
                    pass
        except Exception:
            traceback.print_exc()
        finally:
            if close_window and win_ptr and wm is not None:
                try:
                    windowing.schedule_close(wm, win_ptr)
                except Exception:
                    traceback.print_exc()
            _watchdog_stop()
            self.token += 1          # any still-running modal instance exits
            self._reset_runtime()
            self._set_state(IDLE)
            if reason:
                print(f"[Walkthrough] Stopped: {reason}")
            _tag_redraw_all()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _scene(self):
        for scene in bpy.data.scenes:
            if scene.as_pointer() == self.scene_ptr:
                return scene
        return None

    def check_valid(self, wm) -> str | None:
        """Return a reason string if the session must stop, else None."""
        if self.dedicated is None:
            return "Session has no dedicated window"
        resolved = self.dedicated.resolve(wm)
        if resolved is None:
            return "Walkthrough window or area was closed"
        win = resolved[0]
        if win.scene is None or win.scene.as_pointer() != self.scene_ptr:
            return "Scene changed"
        if self._scene() is None:
            return "Scene was removed"
        if win.view_layer is None or win.view_layer.name != self.view_layer_name:
            return "View layer changed"
        return None

    def validate(self) -> None:
        if not self.is_active:
            return
        try:
            reason = self.check_valid(bpy.context.window_manager)
        except Exception as exc:
            reason = f"Validation failed: {exc}"
        if reason:
            self.stop(reason=reason)

    # ------------------------------------------------------------------
    # Modal event handling
    # ------------------------------------------------------------------
    def handle_event(self, context, event, token: int) -> set:
        if token != self.token or not self.is_active:
            return {"CANCELLED"}
        wm = context.window_manager
        reason = self.check_valid(wm)
        if reason:
            self.stop(reason=reason)
            return {"CANCELLED"}
        win, area, region = self.dedicated.resolve(wm)
        etype = event.type

        if etype == "TIMER":
            if self.state == RUNNING:
                self.tick(area, region)
            return {"PASS_THROUGH"}
        if etype.startswith("TIMER") or etype == "NONE":
            return {"PASS_THROUGH"}   # other timers (reports, jobs, autosave)
        if etype == "WINDOW_DEACTIVATE":
            self.input.clear()
            self._release_mouse(win)
            return {"PASS_THROUGH"}

        if self.mouse.active:
            zone = "OWN"   # captured: cursor is hidden and kept in the region
        else:
            window_rect, passthrough = windowing.routing_rects(area, region)
            zone = classify_point(event.mouse_x, event.mouse_y, window_rect, passthrough)
        if zone != "OWN":
            # Sidebar / outside: let Blender handle it (Pause/Stop buttons).
            if self.input.is_active:
                self.input.clear()
            return {"PASS_THROUGH"}

        # Inside the simulation region every event is consumed so no
        # selection / transform / edit shortcut reaches Blender.
        running = self.state == RUNNING
        if etype in MOUSE_EVENTS:
            if running:
                self._mouse_look(win, region, event)
            return {"RUNNING_MODAL"}

        action = self.input.handle_event(event, paused=not running)
        if action == ACTION_EXIT:
            self.stop(reason="Esc pressed")
            return {"FINISHED"}
        if action == ACTION_PAUSE_TOGGLE:
            if self.state == RUNNING:
                self.pause()
            elif self.state == PAUSED:
                self.resume()
            _tag_redraw_all()
        return {"RUNNING_MODAL"}

    def on_modal_cancel(self, token: int) -> None:
        """Blender removed our modal handler (e.g. its window was closed)."""
        if token != self.token or not self.is_active:
            return
        # Without the modal handler there is no input/update any more, so the
        # session must end. Deferred: we may be inside window teardown.
        bpy.app.timers.register(_make_deferred_stop(token), first_interval=0.0)

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------
    def _mouse_look(self, win, region, event) -> None:
        x, y = event.mouse_x, event.mouse_y
        if not self.mouse.active:
            self.mouse.begin(x, y)
            if not self.cursor_hidden:
                win.cursor_modal_set("NONE")
                self.cursor_hidden = True
            cursor_clip.confine()
            return
        # Re-applied every event: the OS may drop the clip on focus changes.
        cursor_clip.confine()
        dx, dy = self.mouse.motion(x, y)
        self.input.add_look(dx, dy)
        target = self.mouse.warp_request(windowing.region_rect(region))
        if target is not None:
            win.cursor_warp(target[0], target[1])

    def _release_mouse(self, win=None) -> None:
        self.mouse.reset()
        cursor_clip.release()
        if not self.cursor_hidden:
            return
        if win is None and self.dedicated is not None:
            try:
                win = self.dedicated.window(bpy.context.window_manager)
            except Exception:
                win = None
        if win is not None:
            try:
                win.cursor_modal_restore()
            except Exception:
                pass
        self.cursor_hidden = False

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------
    def tick(self, area, region) -> None:
        from . import player as player_mod

        now = time.perf_counter()
        dt = now - self.last_time
        self.last_time = now
        p = self.player

        dx, dy = self.input.consume_look()
        if dx or dy:
            d_yaw, d_pitch = look_angles(dx, dy, self.mouse_sensitivity, self.invert_pitch)
            p.add_look(d_yaw, d_pitch)
        jump, crouch, prone = self.input.consume_edges()
        if crouch:
            p.toggle_posture(player_mod.Posture.CROUCH)
        if prone:
            p.toggle_posture(player_mod.Posture.PRONE)
        forward, right, run = self.input.axes()
        move = player_mod.MoveInput(forward=forward, right=right, run=run, jump=jump)

        t0 = time.perf_counter()
        p.update(dt, move)
        diag = self.diagnostics
        diag.update_time_ms = (time.perf_counter() - t0) * 1000.0
        diag.candidate_triangles = int(getattr(p, "last_candidate_count", 0))
        diag.grounded = bool(p.grounded)
        self._apply_view(area, region)

    def _apply_view(self, area, region) -> None:
        _space, rv3d = windowing.space_and_rv3d(area, region)
        if rv3d is None or self.player is None:
            return
        viewport.apply_first_person(rv3d, self.player.eye_position(),
                                    self.player.view_rotation(), self.view_distance)
        area.tag_redraw()


SESSION = SessionManager()


# ----------------------------------------------------------------------
# Module-level helpers (timers / handlers must be plain functions)
# ----------------------------------------------------------------------
def _make_deferred_stop(token: int):
    def _deferred_stop():
        try:
            if SESSION.token == token and SESSION.is_active:
                SESSION.stop(reason="Input handler was removed (window closed?)")
        except Exception:
            traceback.print_exc()
        return None
    return _deferred_stop


def _tag_redraw_all() -> None:
    """Redraw the sidebars of all 3D Views (state/diagnostics display)."""
    try:
        for win in bpy.context.window_manager.windows:
            screen = win.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass


_last_ui_refresh = 0.0


def _watchdog():
    """Periodic safety net: detects closed windows / scene switches even when
    the modal handler no longer receives events."""
    global _last_ui_refresh
    if not SESSION.is_active:
        return None
    SESSION.validate()
    if not SESSION.is_active:
        return None
    now = time.perf_counter()
    if now - _last_ui_refresh > 0.5:
        _last_ui_refresh = now
        _tag_redraw_all()
    return WATCHDOG_INTERVAL


def _watchdog_start() -> None:
    if not bpy.app.timers.is_registered(_watchdog):
        bpy.app.timers.register(_watchdog, first_interval=WATCHDOG_INTERVAL)


def _watchdog_stop() -> None:
    try:
        if bpy.app.timers.is_registered(_watchdog):
            bpy.app.timers.unregister(_watchdog)
    except Exception:
        pass


@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    # Windows are rebuilt by file loading; do not try to close them here.
    SESSION.stop(reason="File is being loaded", close_window=False)


@bpy.app.handlers.persistent
def _on_validate_handler(*_args):
    # depsgraph / undo / redo: stop if the scene or our window went away.
    if SESSION.is_active:
        SESSION.validate()


_HANDLERS = (
    ("load_pre", _on_load_pre),
    ("depsgraph_update_post", _on_validate_handler),
    ("undo_post", _on_validate_handler),
    ("redo_post", _on_validate_handler),
)


def register():
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn not in lst:
            lst.append(fn)


def unregister():
    try:
        SESSION.stop(reason="Add-on disabled")
    except Exception:
        traceback.print_exc()
    _watchdog_stop()
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        while fn in lst:
            lst.remove(fn)
