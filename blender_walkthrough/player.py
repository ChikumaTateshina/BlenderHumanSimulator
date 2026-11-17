"""First-person player controller (bpy-independent).

Handles posture (stand / crouch / prone), look angles, walking, running,
jumping and gravity on top of ``physics.PhysicsSolver``.  All lengths are
world units; the up axis is Blender's +Z.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from mathutils import Euler, Quaternion, Vector

from .physics import PhysicsSolver

_PITCH_LIMIT = math.radians(89.0)
# Air acceleration expressed relative to walk speed (1/s) so it scales with
# the scene's unit scale: reaches walk speed from rest in 0.2 s.
_AIR_ACCEL_PER_SPEED = 5.0


class Posture(Enum):
    STAND = "STAND"
    CROUCH = "CROUCH"   # C key, crouch_ratio of eye height
    PRONE = "PRONE"     # Z key, prone_ratio of eye height


@dataclass
class PlayerSettings:
    """All lengths in world units."""
    eye_height: float
    walk_speed: float
    run_speed: float
    jump_height: float = 0.5
    gravity: float = 9.81
    radius: float = 0.2
    head_clearance: float = 0.1
    step_height: float = 0.3
    max_slope_deg: float = 45.0
    crouch_ratio: float = 0.55
    prone_ratio: float = 0.15
    posture_blend_time: float = 0.2
    fixed_dt: float = 1.0 / 90.0
    max_substeps: int = 8
    max_frame_dt: float = 0.1


@dataclass
class MoveInput:
    forward: float   # -1..1 (W=+1, S=-1)
    right: float     # -1..1 (D=+1, A=-1)
    run: bool
    jump: bool       # press edge for this frame


def _clamp_pitch(p: float) -> float:
    return max(-_PITCH_LIMIT, min(_PITCH_LIMIT, p))


class PlayerController:
    def __init__(self, solver: PhysicsSolver, settings: PlayerSettings, foot: Vector,
                 yaw: float, pitch: float) -> None:
        self.solver = solver
        self.settings = settings
        self.foot = Vector(foot)
        self.velocity = Vector((0.0, 0.0, 0.0))
        self.yaw = float(yaw)
        self.pitch = _clamp_pitch(float(pitch))
        self.grounded = False
        self.posture = Posture.STAND
        self.current_eye_height = float(settings.eye_height)
        self.last_candidate_count = 0
        self._accumulator = 0.0
        self._jump_pending = False
        self._blend_rate = 0.0  # eye height change per second

    # -- look ------------------------------------------------------------------

    def add_look(self, d_yaw: float, d_pitch: float) -> None:
        self.yaw = math.remainder(self.yaw + d_yaw, math.tau)
        self.pitch = _clamp_pitch(self.pitch + d_pitch)

    def forward_vector(self) -> Vector:
        return Vector((-math.sin(self.yaw), math.cos(self.yaw), 0.0))

    def right_vector(self) -> Vector:
        return Vector((math.cos(self.yaw), math.sin(self.yaw), 0.0))

    def view_rotation(self) -> Quaternion:
        return Euler((math.pi / 2.0 + self.pitch, 0.0, self.yaw), 'XYZ').to_quaternion()

    @staticmethod
    def yaw_pitch_from_rotation(q: Quaternion) -> Tuple[float, float]:
        """Inverse of view_rotation (roll is discarded, pitch clamped to +-89 deg)."""
        q = Quaternion(q).normalized()
        d = q @ Vector((0.0, 0.0, -1.0))          # view direction
        dz = max(-1.0, min(1.0, d.z))
        pitch = math.asin(dz)
        horiz = math.hypot(d.x, d.y)
        if horiz > 1e-6:
            yaw = math.atan2(-d.x, d.y)
        else:
            # Looking straight up/down: derive yaw from the view's up vector,
            # which equals (sin p * sin yaw, -sin p * cos yaw, cos p).
            u = q @ Vector((0.0, 1.0, 0.0))
            s = 1.0 if dz > 0.0 else -1.0
            yaw = math.atan2(u.x * s, -u.y * s)
        return yaw, _clamp_pitch(pitch)

    # -- posture -------------------------------------------------------------------

    def _target_eye(self, posture: Posture) -> float:
        s = self.settings
        if posture == Posture.CROUCH:
            return s.eye_height * s.crouch_ratio
        if posture == Posture.PRONE:
            return s.eye_height * s.prone_ratio
        return s.eye_height

    def _height_for_eye(self, eye: float) -> float:
        s = self.settings
        return max(2.0 * s.radius, eye + s.head_clearance)

    def capsule_height(self) -> float:
        return self._height_for_eye(self.current_eye_height)

    def toggle_posture(self, target: Posture) -> bool:
        """Toggle to target (same posture again -> STAND).  False if blocked."""
        new = Posture.STAND if target == self.posture else target
        new_eye = self._target_eye(new)
        new_h = self._height_for_eye(new_eye)
        if new_h > self.capsule_height() + 1e-9 and not self.solver.is_space_free(self.foot, new_h):
            return False
        self.posture = new
        bt = self.settings.posture_blend_time
        delta = abs(new_eye - self.current_eye_height)
        self._blend_rate = (delta / bt) if bt > 0.0 else math.inf
        return True

    def _update_posture(self, dt: float) -> None:
        target = self._target_eye(self.posture)
        cur = self.current_eye_height
        if cur == target:
            return
        step = self._blend_rate * dt
        if not math.isfinite(step) or step >= abs(target - cur):
            nxt = target
        else:
            nxt = cur + math.copysign(step, target - cur)
        new_h = self._height_for_eye(nxt)
        if new_h > self.capsule_height() + 1e-9 and not self.solver.is_space_free(self.foot, new_h):
            return  # growing is blocked this step; try again later
        self.current_eye_height = nxt

    # -- simulation -------------------------------------------------------------------

    def eye_position(self) -> Vector:
        return self.foot + Vector((0.0, 0.0, self.current_eye_height))

    def update(self, frame_dt: float, inp: MoveInput) -> None:
        """Advance by a frame using a fixed-timestep accumulator."""
        s = self.settings
        if inp.jump:
            self._jump_pending = True
        if not math.isfinite(frame_dt) or frame_dt <= 0.0:
            return
        self._accumulator += min(frame_dt, s.max_frame_dt)
        fdt = s.fixed_dt
        steps = 0
        while self._accumulator >= fdt - 1e-12 and steps < s.max_substeps:
            self._accumulator -= fdt
            jump = self._jump_pending
            self._jump_pending = False
            self._step(fdt, inp, jump)
            steps += 1
        if steps >= s.max_substeps:
            # Drop the backlog rather than spiralling.
            self._accumulator = min(self._accumulator, fdt)
        self._accumulator = max(self._accumulator, 0.0)

    def _step(self, dt: float, inp: MoveInput, jump: bool) -> None:
        s = self.settings
        self._update_posture(dt)

        # Horizontal intent, normalised so diagonals are not faster.
        wish = self.forward_vector() * float(inp.forward) + self.right_vector() * float(inp.right)
        if wish.length > 1.0:
            wish.normalize()
        speed = s.run_speed if inp.run else s.walk_speed
        target = wish * speed
        if self.grounded:
            vh = target
        elif wish.length_squared < 1e-12:
            # No input in the air: keep momentum (jumps, sliding down steep slopes).
            vh = Vector((self.velocity.x, self.velocity.y, 0.0))
        else:
            # Air control: steer the kept momentum towards the input velocity.
            vh = Vector((self.velocity.x, self.velocity.y, 0.0))
            diff = target - vh
            max_dv = _AIR_ACCEL_PER_SPEED * max(s.walk_speed, 1e-6) * dt
            if diff.length > max_dv:
                diff = diff * (max_dv / diff.length)
            vh = vh + diff

        vz = self.velocity.z
        if self.grounded:
            if jump:
                vz = math.sqrt(2.0 * s.gravity * s.jump_height)
                self.grounded = False
            elif vz < 0.0:
                vz = 0.0
        # Midpoint velocity gives the exact parabola for constant gravity.
        vz_mid = vz - 0.5 * s.gravity * dt
        res = self.solver.move(self.foot, Vector((vh.x, vh.y, vz_mid)), dt,
                               self.capsule_height(), self.grounded)
        self.foot = res.foot
        self.grounded = res.grounded
        self.last_candidate_count = res.candidate_count
        new_vz = 0.0 if res.grounded else res.velocity.z - 0.5 * s.gravity * dt
        self.velocity = Vector((res.velocity.x, res.velocity.y, new_vz))
