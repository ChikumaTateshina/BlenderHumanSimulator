"""Unit tests for blender_walkthrough.player (run inside Blender for mathutils)."""

import math
import unittest

from mathutils import Quaternion, Vector

from blender_walkthrough import physics, player
from blender_walkthrough.player import MoveInput, PlayerController, PlayerSettings, Posture
from test_physics import FakeWorld, box, floor, quad, ramp

SKIN = 0.002
IDLE = MoveInput(0.0, 0.0, False, False)


def make_player(tris, foot=(0, 0, SKIN), yaw=0.0, pitch=0.0, **kw):
    world = FakeWorld(tris)
    st = PlayerSettings(eye_height=1.6, walk_speed=3.0, run_speed=5.0, **kw)
    cfg = physics.PhysicsConfig(radius=st.radius, step_height=st.step_height,
                                max_slope_deg=st.max_slope_deg, skin=SKIN)
    solver = physics.PhysicsSolver(world, cfg)
    return PlayerController(solver, st, Vector(foot), yaw, pitch), world


def run(p, seconds, inp=IDLE, frame=1 / 60):
    for _ in range(int(round(seconds / frame))):
        p.update(frame, inp)


class TestMovement(unittest.TestCase):
    def test_settles_on_floor(self):
        p, _ = make_player(floor(), foot=(0, 0, 0.3))
        run(p, 1.0)
        self.assertTrue(p.grounded)
        self.assertAlmostEqual(p.foot.z, SKIN, delta=0.003)
        self.assertAlmostEqual(p.eye_position().z, p.foot.z + 1.6, places=6)

    def test_walk_and_run_speed(self):
        p, _ = make_player(floor())
        run(p, 0.2)
        x0 = p.foot.copy()
        run(p, 1.0, MoveInput(1.0, 0.0, False, False))
        self.assertAlmostEqual((p.foot - x0).y, 3.0, delta=0.08)
        x0 = p.foot.copy()
        run(p, 1.0, MoveInput(1.0, 0.0, True, False))
        self.assertAlmostEqual((p.foot - x0).y, 5.0, delta=0.1)

    def test_diagonal_normalized(self):
        p, _ = make_player(floor())
        run(p, 0.2)
        x0 = p.foot.copy()
        run(p, 1.0, MoveInput(1.0, 1.0, False, False))
        d = p.foot - x0
        self.assertAlmostEqual(d.xy.length, 3.0, delta=0.08)
        self.assertAlmostEqual(d.x, d.y, delta=0.01)   # yaw=0: forward +Y, right +X

    def test_move_follows_yaw(self):
        p, _ = make_player(floor(), yaw=math.pi / 2)  # facing -X
        run(p, 0.2)
        x0 = p.foot.copy()
        run(p, 0.5, MoveInput(1.0, 0.0, False, False))
        d = p.foot - x0
        self.assertLess(d.x, -1.4)
        self.assertAlmostEqual(d.y, 0.0, delta=1e-3)

    def test_jump_height_and_no_double_jump(self):
        p, _ = make_player(floor())
        run(p, 0.3)
        base = p.foot.z
        p.update(1 / 60, MoveInput(0, 0, False, True))
        self.assertFalse(p.grounded)
        max_z = p.foot.z
        jumped_again = False
        for i in range(120):
            vz_before = p.velocity.z
            # Spam jump edges in the air: must be ignored.
            p.update(1 / 60, MoveInput(0, 0, False, i % 5 == 0 and not p.grounded))
            if not p.grounded and p.velocity.z > vz_before + 0.5:
                jumped_again = True
            max_z = max(max_z, p.foot.z)
            if p.grounded:
                break
        self.assertFalse(jumped_again)
        self.assertAlmostEqual(max_z - base, 0.5, delta=0.02)
        self.assertTrue(p.grounded)

    def test_jump_initial_velocity(self):
        p, _ = make_player(floor())
        run(p, 0.3)
        p.update(1 / 90, MoveInput(0, 0, False, True))
        # After one fixed step velocity is v0 - g*dt.
        self.assertAlmostEqual(p.velocity.z, math.sqrt(2 * 9.81 * 0.5) - 9.81 / 90, delta=1e-6)

    def test_ceiling_bump_during_jump(self):
        p, _ = make_player(floor() + quad((-5, -5, 2), (5, -5, 2), (5, 5, 2), (-5, 5, 2)))
        run(p, 0.3)
        p.update(1 / 60, MoveInput(0, 0, False, True))
        max_z = 0.0
        for _ in range(90):
            p.update(1 / 60, IDLE)
            max_z = max(max_z, p.foot.z)
        self.assertLessEqual(max_z + p.capsule_height(), 2.0 + 1e-4)
        self.assertTrue(p.grounded)

    def test_large_frame_dt_clamped(self):
        p, _ = make_player(floor())
        run(p, 0.3)
        x0 = p.foot.copy()
        p.update(5.0, MoveInput(1.0, 0.0, True, False))
        # At most max_substeps fixed steps are simulated.
        self.assertLessEqual((p.foot - x0).length, 8 / 90 * 5.0 + 1e-6)
        self.assertGreater((p.foot - x0).length, 0.0)

    def test_thin_wall_large_dt(self):
        wall = quad((-5, 1, -1), (5, 1, -1), (5, 1, 5), (-5, 1, 5))
        p, _ = make_player(floor() + wall)
        for _ in range(40):
            p.update(1.0, MoveInput(1.0, 0.0, True, False))
        self.assertLessEqual(p.foot.y, 1 - 0.2 + 1e-4)

    def test_step_climb(self):
        p, _ = make_player(floor() + box((-3, 0.6, 0), (3, 10, 0.29)))
        run(p, 1.5, MoveInput(1.0, 0.0, False, False))
        self.assertAlmostEqual(p.foot.z, 0.29 + SKIN, delta=0.004)
        self.assertTrue(p.grounded)


    def _ramp_player(self, angle):
        c = math.cos(math.radians(angle))
        return make_player(ramp(angle), foot=(0, 0, (0.2 + SKIN) / c - 0.2 + 0.005))

    def test_slope_44_stand_and_walk(self):
        p, _ = self._ramp_player(44)
        run(p, 1.0)
        self.assertTrue(p.grounded)
        self.assertLess(abs(p.foot.x), 0.02)            # no sliding
        z0 = p.foot.z
        p.yaw = -math.pi / 2                            # face +X (uphill)
        run(p, 1.0, MoveInput(1.0, 0.0, False, False))
        self.assertGreater(p.foot.z - z0, 1.5)

    def test_slope_46_slides_down(self):
        p, _ = self._ramp_player(46)
        z0 = p.foot.z
        run(p, 1.0)
        self.assertFalse(p.grounded)
        self.assertLess(p.foot.z, z0 - 0.5)
        z1 = p.foot.z
        p.yaw = -math.pi / 2                            # try to run uphill
        run(p, 1.0, MoveInput(1.0, 0.0, True, False))
        self.assertLess(p.foot.z, z1)


class TestPosture(unittest.TestCase):
    def test_toggles(self):
        p, _ = make_player(floor())
        run(p, 0.2)
        self.assertTrue(p.toggle_posture(Posture.CROUCH))
        self.assertEqual(p.posture, Posture.CROUCH)
        run(p, 0.5)
        self.assertAlmostEqual(p.current_eye_height, 0.88, places=6)
        self.assertAlmostEqual(p.capsule_height(), 0.98, places=6)
        self.assertTrue(p.toggle_posture(Posture.CROUCH))   # same again -> STAND
        self.assertEqual(p.posture, Posture.STAND)
        run(p, 0.5)
        self.assertAlmostEqual(p.current_eye_height, 1.6, places=6)
        self.assertTrue(p.toggle_posture(Posture.PRONE))
        run(p, 0.5)
        self.assertAlmostEqual(p.current_eye_height, 0.24, places=6)
        self.assertAlmostEqual(p.capsule_height(), 0.40, places=6)   # min 2r
        self.assertTrue(p.toggle_posture(Posture.CROUCH))   # Z -> C direct switch
        self.assertEqual(p.posture, Posture.CROUCH)
        run(p, 0.5)
        self.assertAlmostEqual(p.current_eye_height, 0.88, places=6)
        self.assertTrue(p.toggle_posture(Posture.PRONE))    # C -> Z direct switch
        self.assertEqual(p.posture, Posture.PRONE)
        self.assertTrue(p.toggle_posture(Posture.PRONE))    # Z again -> STAND
        self.assertEqual(p.posture, Posture.STAND)

    def test_blend_is_smooth(self):
        p, _ = make_player(floor())
        run(p, 0.2)
        p.toggle_posture(Posture.CROUCH)
        p.update(0.1, IDLE)   # half of 0.2 s blend (9 fixed steps)
        self.assertGreater(p.current_eye_height, 0.88 + 0.1)
        self.assertLess(p.current_eye_height, 1.6 - 0.1)

    def test_blocked_stand_up_under_low_ceiling(self):
        ceiling = quad((-5, -5, 1.2), (5, -5, 1.2), (5, 5, 1.2), (-5, 5, 1.2))
        p, _ = make_player(floor() + ceiling)
        # Starts standing inside the ceiling's height -> shrink first.
        p.current_eye_height = 0.88
        p.posture = Posture.CROUCH
        run(p, 0.3)
        self.assertFalse(p.toggle_posture(Posture.CROUCH))   # STAND would hit ceiling
        self.assertEqual(p.posture, Posture.CROUCH)
        self.assertTrue(p.toggle_posture(Posture.PRONE))     # shrinking always ok
        run(p, 0.5)
        self.assertFalse(p.toggle_posture(Posture.PRONE))    # PRONE -> STAND blocked
        self.assertTrue(p.toggle_posture(Posture.CROUCH))    # PRONE -> CROUCH fits (0.98 < 1.2)
        run(p, 0.5)
        self.assertAlmostEqual(p.current_eye_height, 0.88, places=6)

    def test_growth_refused_during_blend(self):
        p, world = make_player(floor())
        p.toggle_posture(Posture.CROUCH)
        run(p, 0.5)
        self.assertTrue(p.toggle_posture(Posture.CROUCH))   # stand up accepted (free space)
        world.add(quad((-5, -5, 1.3), (5, -5, 1.3), (5, 5, 1.3), (-5, 5, 1.3)))
        run(p, 0.5)
        self.assertLessEqual(p.foot.z + p.capsule_height(), 1.3 + 1e-4)
        self.assertLess(p.current_eye_height, 1.6)

    def test_crouch_walks_under_low_gap(self):
        tris = floor() + box((-3, 1, 1.1), (3, 3, 1.5))   # beam 1.1 above floor
        p, _ = make_player(tris)
        run(p, 0.2)
        run(p, 1.0, MoveInput(1.0, 0, False, False))
        self.assertLessEqual(p.foot.y, 1 - 0.2 + 1e-4)       # standing: blocked
        p.toggle_posture(Posture.CROUCH)
        run(p, 0.4)
        run(p, 1.0, MoveInput(1.0, 0, False, False))
        self.assertGreater(p.foot.y, 1.5)                    # crouched: passes


class TestLook(unittest.TestCase):
    def test_pitch_clamp(self):
        p, _ = make_player([])
        p.add_look(0.0, 10.0)
        self.assertAlmostEqual(p.pitch, math.radians(89), places=9)
        p.add_look(0.0, -20.0)
        self.assertAlmostEqual(p.pitch, -math.radians(89), places=9)
        p2, _ = make_player([], pitch=3.0)
        self.assertAlmostEqual(p2.pitch, math.radians(89), places=9)

    def test_view_convention(self):
        p, _ = make_player([])
        d = p.view_rotation() @ Vector((0, 0, -1))
        self.assertLess((d - Vector((0, 1, 0))).length, 1e-6)
        p.add_look(0.0, 0.3)
        d = p.view_rotation() @ Vector((0, 0, -1))
        self.assertGreater(d.z, 0.29)                       # pitch>0 looks up
        p.add_look(0.7, 0.0)
        d = p.view_rotation() @ Vector((0, 0, -1))
        f = p.forward_vector()
        self.assertLess((d.xy.normalized() - f.xy).length, 1e-6)
        self.assertAlmostEqual(f.z, 0.0)

    def test_rotation_round_trip(self):
        for yaw in (-3.0, -1.2, 0.0, 0.4, 1.57, 2.9):
            for pitch in (-1.5, -0.6, 0.0, 0.5, 1.55):
                p, _ = make_player([], yaw=yaw, pitch=pitch)
                y2, p2 = PlayerController.yaw_pitch_from_rotation(p.view_rotation())
                self.assertAlmostEqual(math.remainder(y2 - yaw, math.tau), 0.0, places=5)
                self.assertAlmostEqual(p2, p.pitch, places=5)

    def test_rotation_from_straight_down(self):
        # A Blender top view (identity quaternion) looks straight down -Z.
        yaw, pitch = PlayerController.yaw_pitch_from_rotation(Quaternion())
        self.assertAlmostEqual(pitch, -math.radians(89), places=6)
        self.assertAlmostEqual(yaw, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
