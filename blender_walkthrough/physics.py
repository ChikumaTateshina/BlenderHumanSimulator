"""Capsule vs. triangle collision solver (bpy-independent).

The player is modelled as a vertical capsule whose *foot* is the bottom
point of the capsule (bottom of the lower hemisphere).  All lengths are in
world units.  The collision world is any object exposing
``gather_triangles(aabb_min, aabb_max) -> list[(Vector, Vector, Vector)]``.

Algorithm overview (``PhysicsSolver.move``):

1. Broad phase: one ``gather_triangles`` call with an AABB covering the
   whole possible motion of this sub-step (incl. step-up and ground snap).
2. Slide move: the displacement is split into pieces no longer than half
   the radius (sweep approximation, prevents tunnelling through zero
   thickness walls).  After each piece the capsule is depenetrated with a
   Gauss-Seidel style iteration, and the remaining displacement is clipped
   against the contact planes (sliding).
3. Contacts are classified by their normal (contact point -> capsule axis,
   so triangles are two-sided): FLOOR (angle to up <= max slope), CEILING
   (normal pointing clearly downwards) or WALL.  Floors are resolved by a
   vertical push (no sliding down walkable slopes), walls by a horizontal
   push (no climbing steep slopes), ceilings along the normal.
4. Step-up: when a grounded horizontal move hits a wall, try
   lift -> horizontal move -> drop, accepted only if it lands on walkable
   support no higher than ``step_height`` and makes more progress.
5. Ground probe / snap: while grounded the capsule is snapped down up to
   ``step_height`` so it follows down-slopes and down-steps.

If depenetration does not converge the offending piece of motion is
discarded (the previous, valid position is kept).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from mathutils import Vector
from mathutils.geometry import closest_point_on_tri

# Contact classes
FLOOR = 0
WALL = 1
CEILING = 2

_EPS = 1e-12
# Normals with up-component below this are treated as ceilings.
_CEILING_NZ = -0.3
# Upper bound on sweep pieces for a single slide move (safety valve).
_MAX_PIECES = 256


@dataclass
class PhysicsConfig:
    radius: float                     # capsule radius (world units)
    step_height: float                # max auto step height
    max_slope_deg: float = 45.0       # max walkable slope
    skin: float = 0.002               # contact margin kept between capsule and geometry
    max_iterations: int = 6           # depenetration iterations per piece
    up: Vector = field(default_factory=lambda: Vector((0.0, 0.0, 1.0)))


@dataclass
class MoveResult:
    foot: Vector                      # capsule bottom point
    velocity: Vector                  # velocity after collision response
    grounded: bool
    ground_normal: Optional[Vector]
    hit_ceiling: bool
    candidate_count: int              # number of broad-phase triangles (diagnostics)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _closest_seg_seg(p1: Vector, q1: Vector, p2: Vector, q2: Vector) -> Tuple[Vector, Vector]:
    """Closest points between segments p1q1 and p2q2 (Ericson, RTCD 5.1.9)."""
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = d1.dot(d1)
    e = d2.dot(d2)
    f = d2.dot(r)
    if a <= _EPS and e <= _EPS:
        return p1.copy(), p2.copy()
    if a <= _EPS:
        s = 0.0
        t = _clamp01(f / e)
    else:
        c = d1.dot(r)
        if e <= _EPS:
            t = 0.0
            s = _clamp01(-c / a)
        else:
            b = d1.dot(d2)
            denom = a * e - b * b
            s = _clamp01((b * f - c * e) / denom) if denom > _EPS else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = _clamp01(-c / a)
            elif t > 1.0:
                t = 1.0
                s = _clamp01((b - c) / a)
    return p1 + d1 * s, p2 + d2 * t


def _face_normal(a: Vector, b: Vector, c: Vector) -> Optional[Vector]:
    """Unit face normal, or None for a degenerate (zero area) triangle."""
    n = (b - a).cross(c - a)
    ln = n.length
    # Relative threshold so tiny-but-valid triangles still count.
    scale = max((b - a).length_squared, (c - a).length_squared, (c - b).length_squared)
    if ln <= 1e-10 * max(scale, 1e-20) or ln <= 1e-18:
        return None
    return n / ln


def _closest_impl(p0: Vector, p1: Vector, a: Vector, b: Vector, c: Vector,
                  n: Optional[Vector]) -> Tuple[Vector, Vector, float]:
    """Core of closest_point_segment_triangle with a precomputed unit normal."""
    if n is not None:
        # 1) Does the segment pierce the triangle?
        d0 = (p0 - a).dot(n)
        d1 = (p1 - a).dot(n)
        if (d0 <= 0.0 <= d1) or (d1 <= 0.0 <= d0):
            denom = d0 - d1
            if abs(denom) > _EPS:
                x = p0 + (p1 - p0) * (d0 / denom)
                # Inside test via edge cross products (non-negative = inside).
                if ((b - a).cross(x - a).dot(n) >= -1e-12 and
                        (c - b).cross(x - b).dot(n) >= -1e-12 and
                        (a - c).cross(x - c).dot(n) >= -1e-12):
                    return x, x.copy(), 0.0
        # 2) Segment end points vs. triangle (face/edge/vertex regions).
        best_s = p0
        best_t = closest_point_on_tri(p0, a, b, c)
        best_d2 = (p0 - best_t).length_squared
        q = closest_point_on_tri(p1, a, b, c)
        d2 = (p1 - q).length_squared
        if d2 < best_d2:
            best_s, best_t, best_d2 = p1, q, d2
    else:
        best_s = best_t = None
        best_d2 = math.inf
    # 3) Segment vs. each triangle edge.
    for e0, e1 in ((a, b), (b, c), (c, a)):
        s, t = _closest_seg_seg(p0, p1, e0, e1)
        d2 = (s - t).length_squared
        if d2 < best_d2:
            best_s, best_t, best_d2 = s, t, d2
    return best_s.copy(), best_t.copy(), math.sqrt(best_d2)


def closest_point_segment_triangle(p0: Vector, p1: Vector, a: Vector, b: Vector,
                                   c: Vector) -> Tuple[Vector, Vector, float]:
    """Return (point on segment, point on triangle, distance).

    Handles face, edge and vertex regions, intersecting segments (distance 0)
    and degenerate triangles (treated as their edges).
    """
    p0 = Vector(p0)
    p1 = Vector(p1)
    a = Vector(a)
    b = Vector(b)
    c = Vector(c)
    return _closest_impl(p0, p1, a, b, c, _face_normal(a, b, c))


class _Tri:
    """Candidate triangle with cached AABB and unit normal."""
    __slots__ = ("a", "b", "c", "n", "mnx", "mny", "mnz", "mxx", "mxy", "mxz")

    def __init__(self, a: Vector, b: Vector, c: Vector) -> None:
        self.a = Vector(a)
        self.b = Vector(b)
        self.c = Vector(c)
        self.n = _face_normal(self.a, self.b, self.c)
        self.mnx = min(a[0], b[0], c[0])
        self.mny = min(a[1], b[1], c[1])
        self.mnz = min(a[2], b[2], c[2])
        self.mxx = max(a[0], b[0], c[0])
        self.mxy = max(a[1], b[1], c[1])
        self.mxz = max(a[2], b[2], c[2])


class _Contact:
    __slots__ = ("d", "n", "tp", "kind", "nz", "tri", "face_walkable")

    def __init__(self, d: float, n: Vector, tp: Vector, kind: int, nz: float, tri: _Tri,
                 face_walkable: bool) -> None:
        self.d = d                         # axis-triangle distance
        self.n = n                         # contact normal (triangle -> capsule axis)
        self.tp = tp                       # contact point on the triangle
        self.kind = kind                   # FLOOR / WALL / CEILING
        self.nz = nz                       # n . up
        self.tri = tri
        self.face_walkable = face_walkable  # triangle plane itself is walkable


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

class PhysicsSolver:
    def __init__(self, world, config: PhysicsConfig) -> None:
        self.world = world
        self.config = config
        up = Vector(config.up)
        if up.length_squared <= _EPS:
            up = Vector((0.0, 0.0, 1.0))
        self.up = up.normalized()
        self._cos_slope = math.cos(math.radians(config.max_slope_deg))

    # -- small helpers -----------------------------------------------------

    @property
    def _r(self) -> float:
        return self.config.radius

    def _sep(self) -> float:
        """Target separation after depenetration."""
        return self._r + self.config.skin

    def _touch(self) -> float:
        """Distance below which a triangle counts as a contact."""
        return self._r + 0.5 * self.config.skin

    def _overlap_limit(self) -> float:
        """Distance below which the capsule really overlaps geometry."""
        return self._r - 0.25 * self.config.skin

    def _segment(self, foot: Vector, height: float) -> Tuple[Vector, Vector]:
        r = self._r
        h = max(height, 2.0 * r)
        return foot + self.up * r, foot + self.up * (h - r)

    def _classify(self, nz: float) -> int:
        if nz >= self._cos_slope - 1e-9:
            return FLOOR
        if nz <= _CEILING_NZ:
            return CEILING
        return WALL

    def _gather(self, points: Sequence[Vector], margin: float) -> List[_Tri]:
        mn = [min(p[i] for p in points) - margin for i in range(3)]
        mx = [max(p[i] for p in points) + margin for i in range(3)]
        raw = self.world.gather_triangles(Vector(mn), Vector(mx))
        return [_Tri(t[0], t[1], t[2]) for t in raw]

    def _tri_contact(self, p0: Vector, p1: Vector, tri: _Tri, limit: float,
                     away: Vector) -> Optional[_Contact]:
        """Contact between capsule axis p0p1 and tri if closer than limit."""
        n = tri.n
        if n is not None:
            d0 = (p0 - tri.a).dot(n)
            d1 = (p1 - tri.a).dot(n)
            if (d0 >= limit and d1 >= limit) or (d0 <= -limit and d1 <= -limit):
                return None
        sp, tp, d = _closest_impl(p0, p1, tri.a, tri.b, tri.c, n)
        if d >= limit:
            return None
        if d > 1e-9:
            cn = (sp - tp) / d
        else:
            # Axis touches the triangle: fall back to the face normal oriented
            # towards where we came from, or the hint direction itself.
            if n is not None:
                cn = n.copy() if n.dot(away) >= 0.0 else -n
            else:
                cn = away.copy()
        nz = cn.dot(self.up)
        kind = self._classify(nz)
        face_walkable = True
        if n is not None:
            fz = n.dot(self.up)
            if n.dot(cn) < 0.0:
                fz = -fz
            face_walkable = fz >= self._cos_slope - 1e-6
            # Edge/vertex contacts can produce tilted normals (e.g. the internal
            # diagonal of a steep quad).  Only count them as floor when the
            # triangle itself (oriented towards the capsule) is walkable.
            if kind == FLOOR and not face_walkable:
                kind = WALL
        return _Contact(d, cn, tp, kind, nz, tri, face_walkable)

    def _contacts(self, foot: Vector, height: float, tris: Sequence[_Tri], limit: float,
                  away: Vector) -> List[_Contact]:
        p0, p1 = self._segment(foot, height)
        lo = [min(p0[i], p1[i]) - limit for i in range(3)]
        hi = [max(p0[i], p1[i]) + limit for i in range(3)]
        out: List[_Contact] = []
        for t in tris:
            if (t.mxx < lo[0] or t.mnx > hi[0] or t.mxy < lo[1] or t.mny > hi[1]
                    or t.mxz < lo[2] or t.mnz > hi[2]):
                continue
            c = self._tri_contact(p0, p1, t, limit, away)
            if c is not None:
                out.append(c)
        return out

    def _overlaps(self, foot: Vector, height: float, tris: Sequence[_Tri]) -> bool:
        return bool(self._contacts(foot, height, tris, self._overlap_limit(), self.up))

    # -- public API ----------------------------------------------------------

    def is_space_free(self, foot: Vector, height: float) -> bool:
        """True if a capsule (radius config.radius, total height) overlaps nothing."""
        foot = Vector(foot)
        top = foot + self.up * max(height, 2.0 * self._r)
        tris = self._gather((foot, top), self._r + self.config.skin)
        return not self._overlaps(foot, height, tris)

    def move(self, foot: Vector, velocity: Vector, dt: float, height: float,
             grounded: bool) -> MoveResult:
        """Advance the capsule by one sub-step with collision response."""
        cfg = self.config
        up = self.up
        r = self._r
        foot = Vector(foot)
        vel = Vector(velocity)
        height = max(height, 2.0 * r)
        if not all(math.isfinite(x) for x in vel):
            vel = Vector((0.0, 0.0, 0.0))   # never propagate NaN/inf
            dt = 0.0
        if not (dt > 0.0) or not all(math.isfinite(x) for x in foot):
            return MoveResult(foot, vel, grounded, None, False, 0)

        disp = vel * dt
        vz = vel.dot(up)
        # Broad phase: sphere of radius |disp| around the start capsule plus
        # room for depenetration, step-up and ground snapping.
        margin = disp.length + 2.0 * r + cfg.step_height + 4.0 * cfg.skin
        tris = self._gather((foot, foot + up * height), margin)

        pos_a, contacts_a, wall_a, _ = self._slide(foot, disp, height, tris)
        pos, contacts = pos_a, contacts_a
        ground = None
        stepped = False

        hdisp = disp - up * disp.dot(up)
        if grounded and vz <= 1e-6 and wall_a and hdisp.length > 1e-9 and cfg.step_height > 0.0:
            step = self._try_step(foot, hdisp, height, tris, pos_a)
            if step is not None:
                pos, contacts, ground = step
                stepped = True

        # Velocity response from the contacts of the chosen path.
        v = vel.copy()
        walls: List[Vector] = []
        hit_ceiling = False
        for n, kind in contacts:
            if kind == CEILING:
                hit_ceiling = True
            v = self._clip(v, n, kind, walls)

        if not stepped and vz <= 1e-6 and v.dot(up) <= 1e-6:
            max_drop = (cfg.step_height + 2.0 * cfg.skin) if grounded else 2.0 * cfg.skin
            status, g = self._probe(pos, height, max_drop, tris, roll_off_edges=grounded)
            if status == "floor":
                pos, ground = g[0], g
        is_grounded = ground is not None
        if is_grounded:
            vu = v.dot(up)
            if vu < 0.0:
                v = v - up * vu
        return MoveResult(
            foot=pos,
            velocity=v,
            grounded=is_grounded,
            ground_normal=ground[1].copy() if is_grounded else None,
            hit_ceiling=hit_ceiling,
            candidate_count=len(tris),
        )

    # -- internals -------------------------------------------------------------

    def _push(self, pos: Vector, c: _Contact, pen: float) -> Vector:
        up = self.up
        if c.kind == FLOOR:
            # Vertical push: standing on walkable slopes does not slide.
            return pos + up * (pen / max(c.nz, 1e-6))
        if c.kind == WALL:
            # Horizontal push: walking into steep slopes does not climb them.
            h = c.n - up * c.nz
            hl2 = h.length_squared
            if hl2 > 0.04:
                return pos + h * (pen / hl2)
        return pos + c.n * pen

    def _resolve(self, target: Vector, height: float, tris: Sequence[_Tri], motion: Vector,
                 forbid_floor: bool) -> Tuple[Vector, bool, List[Tuple[Vector, int]]]:
        """Iteratively push the capsule out of geometry.

        Returns (position, ok, [(normal, kind)] of resolved contacts).
        """
        touch = self._touch()
        sep = self._sep()
        ml = motion.length
        away = (-motion / ml) if ml > 1e-12 else self.up.copy()
        pos = target.copy()
        resolved: List[Tuple[Vector, int]] = []
        for _ in range(max(1, self.config.max_iterations)):
            cs = self._contacts(pos, height, tris, touch, away)
            if not cs:
                return pos, True, resolved
            # Floors first (so edges of step tops win over risers), deepest first.
            cs.sort(key=lambda c: (c.kind != FLOOR, c.d))
            first = True
            for c in cs:
                if not first:
                    p0, p1 = self._segment(pos, height)
                    c = self._tri_contact(p0, p1, c.tri, touch, away)
                    if c is None:
                        continue
                first = False
                if forbid_floor and c.kind == FLOOR:
                    return target, False, resolved
                pos = self._push(pos, c, sep - c.d)
                resolved.append((c.n, c.kind))
        # Not converged within the iteration budget: accept only if no real overlap.
        return pos, not self._overlaps(pos, height, tris), resolved

    def _clip(self, x: Vector, n: Vector, kind: int, walls: List[Vector]) -> Vector:
        """Remove the component of x moving into a contact (sliding)."""
        up = self.up
        xz = x.dot(up)
        if kind == FLOOR:
            if xz < 0.0:
                x = x - up * xz
            return x
        if kind == CEILING:
            xn = x.dot(n)
            if xn < 0.0:
                x = x - n * xn
            xz = x.dot(up)
            if xz > 0.0:
                x = x - up * xz
            return x
        # WALL: clip only the horizontal part against the horizontal normal.
        nz = n.dot(up)
        h = n - up * nz
        hl = h.length
        if hl < 1e-6:
            return x
        h = h / hl
        xh = x.dot(h)
        if xh < 0.0:
            x = x - h * xh
            # Crease between two walls: horizontal motion is blocked.
            for w in walls:
                if x.dot(w) < -1e-9:
                    x = up * x.dot(up)
                    break
            walls.append(h)
        if nz > 0.0:
            xz = x.dot(up)
            if xz < 0.0:
                # Falling onto a steep slope: redirect into a slide along it.
                vz = up * xz
                x = x - vz + (vz - n * vz.dot(n))
        return x

    def _slide(self, pos: Vector, disp: Vector, height: float, tris: Sequence[_Tri],
               forbid_floor: bool = False):
        """Substepped collide-and-slide.  Returns (pos, contacts, hit_wall, failed)."""
        rem = disp.copy()
        contacts: List[Tuple[Vector, int]] = []
        walls: List[Vector] = []
        hit_wall = False
        failed = False
        max_piece = 0.5 * self._r
        pieces = 0
        pos = pos.copy()
        while rem.length_squared > 1e-16 and pieces < _MAX_PIECES:
            pieces += 1
            ln = rem.length
            step = rem.copy() if ln <= max_piece else rem * (max_piece / ln)
            new, ok, cs = self._resolve(pos + step, height, tris, step, forbid_floor)
            if not ok:
                # Safe fallback: keep the last valid position.
                failed = True
                break
            pos = new
            rem = rem - step
            for n, kind in cs:
                contacts.append((n, kind))
                if kind == WALL:
                    hit_wall = True
                rem = self._clip(rem, n, kind, walls)
        return pos, contacts, hit_wall, failed

    def _settle(self, q: Vector, height: float, tris: Sequence[_Tri]):
        """Lift q vertically out of walkable contacts.  Returns (status, data)."""
        up = self.up
        touch = self._touch()
        sep = self._sep()
        best: Optional[_Contact] = None
        for _ in range(4):
            cs = self._contacts(q, height, tris, touch, up)
            lift = 0.0
            for c in cs:
                if c.kind == FLOOR:
                    l = (sep - c.d) / max(c.nz, 1e-6)
                    if l > lift:
                        lift, best = l, c
            if lift <= 1e-9:
                break
            q = q + up * lift
        if best is None:
            return "blocked", None
        if self._overlaps(q, height, tris):
            return "blocked", None
        return "floor", (q, best.n, best.tp)

    def _probe(self, pos: Vector, height: float, max_drop: float, tris: Sequence[_Tri],
               roll_off_edges: bool = False):
        """Sweep the capsule down by up to max_drop looking for walkable support.

        With roll_off_edges, hitting the convex edge of a walkable surface
        (e.g. a stair nosing when walking down) pushes the capsule
        horizontally off the edge (at most one radius in total) and keeps
        descending, instead of giving up.

        Returns ("floor", (pos, normal, support_point)), ("blocked", None) when
        something non-walkable was hit first, or ("none", None).
        """
        up = self.up
        touch = self._touch()
        sep = self._sep()
        piece = 0.5 * self._r
        traveled = 0.0
        p = pos.copy()
        while traveled < max_drop - 1e-12:
            step = min(piece, max_drop - traveled)
            free = p
            p = p - up * step
            traveled += step
            for it in range(max(1, self.config.max_iterations)):
                cs = self._contacts(p, height, tris, touch, up)
                if not cs:
                    break
                if any(c.kind == FLOOR for c in cs):
                    return self._settle(p, height, tris)
                if it == 0:
                    # Edge normals depend on penetration depth: classify at the
                    # first-touch height found by bisection, not at the piece end.
                    p, cs = self._first_touch(free, p, height, tris)
                    if any(c.kind == FLOOR for c in cs):
                        return self._settle(p, height, tris)
                if not roll_off_edges or not any(c.face_walkable for c in cs) \
                        or any(c.nz < -1e-3 for c in cs):
                    return "blocked", None
                for c in cs:
                    pc = self._tri_contact(*self._segment(p, height), c.tri, touch, up)
                    if pc is not None:
                        p = self._push(p, pc, sep - pc.d)
                off = p - pos
                if (off - up * off.dot(up)).length > self._r:
                    return "blocked", None
            else:
                return "blocked", None
        return "none", None

    def _first_touch(self, free: Vector, hit: Vector, height: float, tris: Sequence[_Tri],
                     iterations: int = 10):
        """Bisect between a free and a touching position; return (pos, contacts)."""
        touch = self._touch()
        lo, hi = free, hit
        cs_hi = self._contacts(hi, height, tris, touch, self.up)
        for _ in range(iterations):
            mid = (lo + hi) * 0.5
            cs = self._contacts(mid, height, tris, touch, self.up)
            if cs:
                hi, cs_hi = mid, cs
            else:
                lo = mid
        return hi, cs_hi

    def _try_step(self, start: Vector, hdisp: Vector, height: float, tris: Sequence[_Tri],
                  pos_a: Vector):
        """Step-up attempt: lift, move horizontally, drop onto walkable support."""
        cfg = self.config
        up = self.up
        sh = cfg.step_height
        lift_pos, _, _, _ = self._slide(start, up * sh, height, tris)
        lift = (lift_pos - start).dot(up)
        if lift <= cfg.skin:
            return None  # head blocked
        ln = hdisp.length
        hdir = hdisp / ln
        prog_a = (pos_a - start).dot(hdir)
        # First try the requested distance; if that only reaches the edge of
        # the step (non-walkable contact), retry with half a radius so the
        # lower hemisphere can get over the edge even at low speeds.
        tries = [ln] if ln >= 0.5 * self._r else [ln, 0.5 * self._r]
        for dist in tries:
            mid, mc, _, _ = self._slide(lift_pos, hdir * dist, height, tris, forbid_floor=True)
            status, g = self._probe(mid, height, lift + sh, tris)
            if status == "blocked":
                continue
            if status != "floor":
                return None
            final, _n, support = g
            if (support - start).dot(up) > sh + 1e-6:
                return None  # support too high (e.g. 0.31 step)
            rise = (final - start).dot(up)
            if rise <= cfg.skin or rise > sh + 2.0 * cfg.skin:
                return None  # did not actually climb anything
            if (final - start).dot(hdir) <= prog_a + 1e-6:
                return None  # no better than the plain move
            if self._overlaps(final, height, tris):
                return None
            return final, mc, g
        return None
