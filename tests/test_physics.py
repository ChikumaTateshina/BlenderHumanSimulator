"""Unit tests for blender_walkthrough.physics (run inside Blender for mathutils)."""

import math
import random
import unittest

from mathutils import Vector

from blender_walkthrough import physics

R = 0.2
SKIN = 0.002


# ---------------------------------------------------------------------------
# Test fixtures shared with test_player.py
# ---------------------------------------------------------------------------

class FakeWorld:
    """Brute-force stand-in for CollisionWorld.gather_triangles."""

    def __init__(self, tris=None):
        self.tris = [tuple(Vector(v) for v in t) for t in (tris or [])]
        self.queries = 0

    def add(self, tris):
        self.tris.extend(tuple(Vector(v) for v in t) for t in tris)

    def gather_triangles(self, aabb_min, aabb_max):
        self.queries += 1
        out = []
        for t in self.tris:
            if all(min(v[i] for v in t) <= aabb_max[i] and max(v[i] for v in t) >= aabb_min[i]
                   for i in range(3)):
                out.append(t)
        return out


def quad(p0, p1, p2, p3):
    p0, p1, p2, p3 = (Vector(p) for p in (p0, p1, p2, p3))
    return [(p0, p1, p2), (p0, p2, p3)]


def floor(z=0.0, size=20.0):
    s = size
    return quad((-s, -s, z), (s, -s, z), (s, s, z), (-s, s, z))


def box(mn, mx):
    x0, y0, z0 = mn
    x1, y1, z1 = mx
    v = [Vector(p) for p in ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    out = []
    for a, b, c, d in faces:
        out += quad(v[a], v[b], v[c], v[d])
    return out


def ramp(angle_deg, size=20.0):
    """Plane z = x*tan(angle) through the origin."""
    t = math.tan(math.radians(angle_deg))
    s = size
    return quad((-s, -s, -s * t), (s, -s, s * t), (s, s, s * t), (-s, s, -s * t))


def make_solver(tris, step=0.3, slope=45.0):
    world = FakeWorld(tris)
    cfg = physics.PhysicsConfig(radius=R, step_height=step, max_slope_deg=slope, skin=SKIN)
    return physics.PhysicsSolver(world, cfg), world


def simulate(solver, foot, horiz, seconds, height=1.7, g=9.81, dt=1.0 / 90.0, grounded=False):
    """Simple driver: constant horizontal velocity + gravity."""
    foot = Vector(foot)
    vz = 0.0
    res = None
    for _ in range(int(round(seconds / dt))):
        if grounded:
            vz = 0.0
        vel = Vector((horiz[0], horiz[1], vz - 0.5 * g * dt))
        res = solver.move(foot, vel, dt, height, grounded)
        foot = res.foot
        grounded = res.grounded
        vz = 0.0 if grounded else res.velocity.z - 0.5 * g * dt
    return foot, grounded, res


# ---------------------------------------------------------------------------
# Narrow phase
# ---------------------------------------------------------------------------

class TestSegmentTriangle(unittest.TestCase):
    A = Vector((0, 0, 0))
    B = Vector((1, 0, 0))
    C = Vector((0, 1, 0))

    def check(self, p0, p1, expect_d, expect_tp=None):
        sp, tp, d = physics.closest_point_segment_triangle(Vector(p0), Vector(p1),
                                                           self.A, self.B, self.C)
        self.assertAlmostEqual(d, expect_d, places=6)
        self.assertAlmostEqual((sp - tp).length, d, places=6)
        if expect_tp is not None:
            self.assertLess((tp - Vector(expect_tp)).length, 1e-6)

    def test_face_region(self):
        self.check((0.2, 0.2, 1), (0.2, 0.2, 2), 1.0, (0.2, 0.2, 0))

    def test_below_face_two_sided(self):
        self.check((0.2, 0.2, -2), (0.2, 0.2, -0.5), 0.5, (0.2, 0.2, 0))

    def test_edge_region(self):
        # Closest feature is the hypotenuse edge.
        self.check((1, 1, 0), (1, 1, 1), math.sqrt(0.5), (0.5, 0.5, 0))

    def test_vertex_region(self):
        self.check((-1, -1, 0), (-1, -1, 3), math.sqrt(2), (0, 0, 0))

    def test_intersection(self):
        self.check((0.2, 0.2, -1), (0.2, 0.2, 1), 0.0, (0.2, 0.2, 0))

    def test_parallel_segment_edge_vs_edge(self):
        # Horizontal segment crossing above-outside the edge y=0.
        self.check((0.5, -1, 0.0), (0.5, -1, 0.0), 1.0, (0.5, 0, 0))
        self.check((-1, -0.5, 0.5), (2, -0.5, 0.5), math.sqrt(0.5), None)

    def test_degenerate_triangle(self):
        a, b, c = Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((2, 0, 0))
        sp, tp, d = physics.closest_point_segment_triangle(Vector((0.5, 1, 0)), Vector((0.5, 1, 1)),
                                                           a, b, c)
        self.assertAlmostEqual(d, 1.0, places=6)
        p = Vector((3, 3, 3))
        sp, tp, d = physics.closest_point_segment_triangle(p, p, p, p, p)
        self.assertAlmostEqual(d, 0.0, places=6)

    def test_random_against_sampling(self):
        rnd = random.Random(1234)

        def rv():
            return Vector((rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1)))

        for _ in range(60):
            p0, p1, a, b, c = rv(), rv(), rv(), rv(), rv()
            sp, tp, d = physics.closest_point_segment_triangle(p0, p1, a, b, c)
            # Returned points must lie on the primitives and match the distance.
            self.assertAlmostEqual((sp - tp).length, d, places=6)
            # Brute-force upper bound by sampling.
            best = math.inf
            n = 24
            for i in range(n + 1):
                s = p0 + (p1 - p0) * (i / n)
                for u in range(n + 1):
                    for v in range(n + 1 - u):
                        q = a + (b - a) * (u / n) + (c - a) * (v / n)
                        best = min(best, (s - q).length)
            self.assertLessEqual(d, best + 1e-6)
            self.assertGreater(d, best - 0.12)  # sampling resolution


# ---------------------------------------------------------------------------
# Solver behaviour
# ---------------------------------------------------------------------------

class TestSolver(unittest.TestCase):
    def test_is_space_free(self):
        solver, _ = make_solver(floor() + box((1, -1, 0), (2, 1, 2)))
        self.assertTrue(solver.is_space_free(Vector((0, 0, 0.01)), 1.7))
        self.assertFalse(solver.is_space_free(Vector((1.5, 0, 0.5)), 1.7))
        self.assertFalse(solver.is_space_free(Vector((0, 0, -0.1)), 1.7))
        self.assertFalse(solver.is_space_free(Vector((0.9, 0, 0.01)), 1.7))

    def test_standing_on_floor(self):
        solver, _ = make_solver(floor())
        foot, grounded, res = simulate(solver, (0, 0, 0.05), (0, 0), 1.0)
        self.assertTrue(grounded)
        self.assertAlmostEqual(foot.z, SKIN, delta=0.002)
        self.assertLess(abs(foot.x) + abs(foot.y), 1e-6)
        self.assertGreater(res.candidate_count, 0)
        self.assertIsNotNone(res.ground_normal)
        self.assertGreater(res.ground_normal.z, 0.99)

    def test_fall_and_land(self):
        solver, _ = make_solver(floor())
        foot, grounded, _ = simulate(solver, (0, 0, 3.0), (0, 0), 0.3)
        self.assertFalse(grounded)
        self.assertLess(foot.z, 3.0)
        foot, grounded, _ = simulate(solver, foot, (0, 0), 1.5)
        self.assertTrue(grounded)
        self.assertAlmostEqual(foot.z, SKIN, delta=0.002)

    def test_fall_onto_single_sided_thin_floor_fast(self):
        # Zero-thickness floor plate, hitting it at high speed with big dt.
        solver, _ = make_solver(quad((-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)))
        res = solver.move(Vector((0, 0, 5)), Vector((0, 0, -60)), 0.2, 1.7, False)
        self.assertGreater(res.foot.z, -1e-6)
        self.assertTrue(res.grounded)

    def test_wall_stop(self):
        solver, _ = make_solver(floor() + box((1, -5, 0), (1.5, 5, 3)))
        foot, grounded, _ = simulate(solver, (0, 0, SKIN), (3, 0), 1.5, grounded=True)
        self.assertTrue(grounded)
        self.assertLessEqual(foot.x, 1 - R + 1e-4)
        self.assertGreater(foot.x, 1 - R - 0.01)
        self.assertAlmostEqual(foot.z, SKIN, delta=0.003)

    def test_wall_slide_diagonal(self):
        solver, _ = make_solver(floor() + box((1, -10, 0), (1.5, 10, 3)))
        v = 3 / math.sqrt(2)
        foot, _, res = simulate(solver, (0, 0, SKIN), (v, v), 1.0, grounded=True)
        self.assertLessEqual(foot.x, 1 - R + 1e-4)
        # Keeps sliding along the wall at roughly the tangential speed.
        self.assertGreater(foot.y, 0.9 * v * 1.0 - 0.2)
        self.assertAlmostEqual(res.velocity.x, 0.0, places=4)
        # Step-up attempts along the wall must not speed the slide up.
        self.assertLessEqual(foot.y, v * 1.0 + 0.01)
        self.assertAlmostEqual(foot.z, SKIN, delta=0.003)

    def test_stairs(self):
        # 6 steps, rise 0.25, tread 0.3, then a landing.
        tris = floor()
        for i in range(6):
            tris += box((1 + 0.3 * i, -2, 0), (10, 2, 0.25 * (i + 1)))
        solver, _ = make_solver(tris)
        foot, grounded, _ = simulate(solver, (0, 0, SKIN), (3, 0), 2.5, grounded=True)
        self.assertTrue(grounded)
        self.assertAlmostEqual(foot.z, 1.5 + SKIN, delta=0.004)
        self.assertGreater(foot.x, 3.0)
        # And back down again, staying grounded via snapping.
        foot, grounded, _ = simulate(solver, foot, (-3, 0), 2.5, grounded=True)
        self.assertTrue(grounded)
        self.assertAlmostEqual(foot.z, SKIN, delta=0.004)

    def test_corner_crease(self):
        tris = floor() + box((1, -5, 0), (1.5, 5, 3)) + box((-5, 1, 0), (5, 1.5, 3))
        solver, _ = make_solver(tris)
        foot, _, _ = simulate(solver, (0, 0, SKIN), (3, 3), 1.0, grounded=True)
        self.assertLessEqual(foot.x, 1 - R + 1e-4)
        self.assertLessEqual(foot.y, 1 - R + 1e-4)
        self.assertGreater(foot.x, 1 - R - 0.02)
        self.assertGreater(foot.y, 1 - R - 0.02)

    def test_ceiling_bump(self):
        solver, _ = make_solver(floor() + quad((-5, -5, 2), (5, -5, 2), (5, 5, 2), (-5, 5, 2)))
        foot = Vector((0, 0, SKIN))
        dt = 1 / 90
        vz = math.sqrt(2 * 9.81 * 0.5)
        max_z = 0.0
        hit = False
        grounded = False
        for i in range(120):
            res = solver.move(foot, Vector((0, 0, vz - 0.5 * 9.81 * dt)), dt, 1.7, grounded)
            foot, grounded = res.foot, res.grounded
            hit = hit or res.hit_ceiling
            if res.hit_ceiling:
                self.assertLessEqual(res.velocity.z, 0.0)
            vz = 0.0 if grounded else res.velocity.z - 0.5 * 9.81 * dt
            max_z = max(max_z, foot.z)
        self.assertTrue(hit)
        self.assertLessEqual(max_z, 2.0 - 1.7 + 1e-4)
        self.assertGreater(max_z, 0.25)
        self.assertTrue(grounded)
        self.assertAlmostEqual(foot.z, SKIN, delta=0.003)

    def _step_case(self, h, speed=3.0):
        solver, _ = make_solver(floor() + box((0.5, -3, 0), (10, 3, h)))
        return simulate(solver, (0, 0, SKIN), (speed, 0), 1.5, grounded=True)

    def test_step_029_climbs(self):
        for speed in (3.0, 5.0, 0.5):
            foot, grounded, _ = self._step_case(0.29, speed)
            self.assertTrue(grounded, speed)
            self.assertAlmostEqual(foot.z, 0.29 + SKIN, delta=0.004, msg=str(speed))
            self.assertGreater(foot.x, 0.55, speed)

    def test_step_031_blocks(self):
        for speed in (3.0, 5.0, 0.5):
            foot, grounded, _ = self._step_case(0.31, speed)
            self.assertTrue(grounded)
            self.assertAlmostEqual(foot.z, SKIN, delta=0.004)
            self.assertLessEqual(foot.x, 0.5 - R + 1e-4)

    def test_step_down_snaps(self):
        # Walk off a 0.25 ledge: should stay grounded (snap) and end on the low floor.
        solver, _ = make_solver(floor(-0.25) + box((-5, -5, -0.25), (0, 5, 0)))
        foot, grounded, _ = simulate(solver, (-1, 0, SKIN), (3, 0), 1.0, grounded=True)
        self.assertTrue(grounded)
        self.assertAlmostEqual(foot.z, -0.25 + SKIN, delta=0.004)

    def test_low_head_blocks_step(self):
        # 0.2 step, but ceiling above the step leaves no head room for 1.7 capsule.
        tris = floor() + box((0.5, -3, 0), (3, 3, 0.2)) + quad((0.3, -3, 1.8), (3, -3, 1.8),
                                                               (3, 3, 1.8), (0.3, 3, 1.8))
        solver, _ = make_solver(tris)
        foot, _, _ = simulate(solver, (0, 0, SKIN), (3, 0), 1.0, grounded=True)
        self.assertLess(foot.z, 0.05)
        self.assertLessEqual(foot.x, 0.5 - R + 1e-4)

    @staticmethod
    def _ramp_start(angle):
        c = math.cos(math.radians(angle))
        return Vector((0, 0, (R + SKIN) / c - R + 0.005))

    def test_slope_44_walkable(self):
        solver, _ = make_solver(ramp(44))
        start = self._ramp_start(44)
        foot, grounded, _ = simulate(solver, start, (0, 0), 1.0)
        self.assertTrue(grounded)
        self.assertLess((foot - start).length, 0.02)   # does not slide down
        foot2, grounded, _ = simulate(solver, foot, (3, 0), 1.0, grounded=True)
        self.assertTrue(grounded)
        self.assertGreater(foot2.z - foot.z, 1.5)       # walks uphill
        foot3, grounded, _ = simulate(solver, foot2, (-3, 0), 1.0, grounded=True)
        self.assertTrue(grounded)                       # follows downhill without flying
        self.assertLess(foot3.z, foot2.z - 1.5)

    def test_slope_46_not_walkable(self):
        solver, _ = make_solver(ramp(46))
        start = self._ramp_start(46)
        foot, grounded, _ = simulate(solver, start, (0, 0), 1.0)
        self.assertFalse(grounded)
        # Slides down (slowly here because this driver discards horizontal
        # momentum; PlayerController keeps it, see test_player).
        self.assertLess(foot.z, start.z - 0.15)
        # Trying to walk up does not gain height.
        start2 = foot
        foot2, grounded2, _ = simulate(solver, start2, (3, 0), 1.0)
        self.assertFalse(grounded2)
        self.assertLess(foot2.z, start2.z)

    def test_thin_wall_no_tunnel_run_speed(self):
        wall = quad((1, -5, -1), (1, 5, -1), (1, 5, 5), (1, -5, 5))   # zero thickness
        solver, _ = make_solver(floor() + wall)
        foot, _, _ = simulate(solver, (0, 0, SKIN), (5, 0), 2.0, grounded=True)
        self.assertLessEqual(foot.x, 1 - R + 1e-4)
        # From the other side (two-sided).
        foot, _, _ = simulate(solver, (2, 0, SKIN), (-5, 0), 2.0, grounded=True)
        self.assertGreaterEqual(foot.x, 1 + R - 1e-4)

    def test_thin_wall_no_tunnel_large_dt(self):
        wall = quad((1, -5, -1), (1, 5, -1), (1, 5, 5), (1, -5, 5))
        solver, _ = make_solver(floor() + wall)
        for v, dt in ((5.0, 0.1), (5.0, 1.0), (50.0, 0.1), (200.0, 0.05)):
            res = solver.move(Vector((0, 0, SKIN)), Vector((v, 0, 0)), dt, 1.7, True)
            self.assertLessEqual(res.foot.x, 1 - R + 1e-4, (v, dt))
            if v * dt > 1.0:   # reached the wall
                self.assertAlmostEqual(res.velocity.x, 0.0, places=4)

    def test_diagonal_thin_wall_edge_on(self):
        # Approaching a thin wall along its plane hits its end edge, not passes through.
        wall = quad((1, 0, -1), (3, 0, -1), (3, 0, 5), (1, 0, 5))
        solver, _ = make_solver(floor() + wall)
        foot, _, _ = simulate(solver, (0, 0, SKIN), (5, 0), 1.0, grounded=True)
        self.assertLessEqual(foot.x, 1 - R + 1e-3)

    def test_degenerate_triangles_ignored_safely(self):
        tris = floor() + [(Vector((0.5, 0, 0)), Vector((0.5, 0, 0)), Vector((0.5, 0, 0))),
                          (Vector((0, 1, 0)), Vector((1, 1, 0)), Vector((2, 1, 0)))]
        solver, _ = make_solver(tris)
        foot, grounded, _ = simulate(solver, (0, 0, 0.05), (0, 0), 0.5)
        self.assertTrue(grounded)
        self.assertTrue(all(math.isfinite(x) for x in foot))

    def test_no_move_when_embedded_and_stuck(self):
        # Inside a closed tiny box: can never become valid -> position is kept.
        solver, _ = make_solver(box((-0.1, -0.1, 0.5), (0.1, 0.1, 0.7)))
        start = Vector((0, 0, 0.0))
        res = solver.move(start, Vector((1, 0, 0)), 1 / 90, 1.7, False)
        self.assertTrue(all(math.isfinite(x) for x in res.foot))

    def test_broad_phase_is_local(self):
        solver, world = make_solver(floor())
        far = [(Vector((100 + i, 0, 0)), Vector((101 + i, 0, 0)), Vector((100 + i, 1, 0)))
               for i in range(50)]
        world.add(far)
        res = solver.move(Vector((0, 0, SKIN)), Vector((3, 0, -0.05)), 1 / 90, 1.7, True)
        self.assertEqual(res.candidate_count, 2)


if __name__ == "__main__":
    unittest.main()
