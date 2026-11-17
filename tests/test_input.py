"""Unit tests for blender_walkthrough.input (pure logic)."""
import math
import unittest

from blender_walkthrough import input as winput
from blender_walkthrough.input import (ACTION_EXIT, ACTION_PAUSE_TOGGLE, InputState,
                                       MouseLook, classify_point, look_angles)


class _Ev:
    def __init__(self, t, v, rep=False):
        self.type, self.value, self.is_repeat = t, v, rep


class TestInputState(unittest.TestCase):
    def setUp(self):
        self.s = InputState()

    def press(self, k, rep=False, paused=False):
        return self.s.handle_key(k, "PRESS", rep, paused)

    def release(self, k):
        return self.s.handle_key(k, "RELEASE")

    def test_wasd_axes(self):
        self.press("W")
        self.assertEqual(self.s.axes(), (1.0, 0.0, False))
        self.press("D")
        self.assertEqual(self.s.axes(), (1.0, 1.0, False))
        self.press("S")  # W+S cancel
        self.assertEqual(self.s.axes()[0], 0.0)
        self.release("W")
        self.release("S")
        self.press("A")
        self.release("D")
        self.assertEqual(self.s.axes(), (0.0, -1.0, False))

    def test_run_either_shift(self):
        self.press("LEFT_SHIFT")
        self.assertTrue(self.s.axes()[2])
        self.press("RIGHT_SHIFT")
        self.release("LEFT_SHIFT")
        self.assertTrue(self.s.axes()[2])
        self.release("RIGHT_SHIFT")
        self.assertFalse(self.s.axes()[2])

    def test_jump_edge_ignores_repeat_and_hold(self):
        self.press("SPACE")
        self.assertEqual(self.s.consume_edges(), (True, False, False))
        # Held: OS repeat and duplicate PRESS without RELEASE do nothing
        self.press("SPACE", rep=True)
        self.press("SPACE")
        self.assertEqual(self.s.consume_edges(), (False, False, False))
        self.release("SPACE")
        self.press("SPACE")
        self.assertEqual(self.s.consume_edges(), (True, False, False))

    def test_crouch_prone_edges(self):
        self.press("C")
        self.press("C", rep=True)
        self.press("Z")
        self.assertEqual(self.s.consume_edges(), (False, True, True))
        self.assertEqual(self.s.consume_edges(), (False, False, False))

    def test_actions(self):
        self.assertEqual(self.press("ESC"), ACTION_EXIT)
        self.assertIsNone(self.press("ESC", rep=True))
        self.assertEqual(self.press("P"), ACTION_PAUSE_TOGGLE)
        self.assertEqual(self.press("ESC", paused=True), ACTION_EXIT)
        self.assertEqual(self.press("P", paused=True), ACTION_PAUSE_TOGGLE)
        self.assertIsNone(self.release("P"))

    def test_paused_ignores_movement(self):
        self.press("W", paused=True)
        self.press("SPACE", paused=True)
        self.assertEqual(self.s.axes(), (0.0, 0.0, False))
        self.assertEqual(self.s.consume_edges(), (False, False, False))

    def test_unrelated_keys_ignored(self):
        for k in ("G", "R", "TAB", "X", "LEFTMOUSE"):
            self.assertIsNone(self.press(k))
        self.assertFalse(self.s.is_active)

    def test_clear(self):
        self.press("W")
        self.press("C")
        self.s.add_look(5, 3)
        self.assertTrue(self.s.is_active)
        self.s.clear()
        self.assertFalse(self.s.is_active)
        self.assertEqual(self.s.axes(), (0.0, 0.0, False))
        self.assertEqual(self.s.consume_look(), (0.0, 0.0))
        # After clear a still-held key needs a fresh press to trigger
        self.press("C")
        self.assertEqual(self.s.consume_edges(), (False, True, False))

    def test_handle_event_wrapper(self):
        self.s.handle_event(_Ev("W", "PRESS"))
        self.assertEqual(self.s.axes()[0], 1.0)

    def test_look_accumulates(self):
        self.s.add_look(2, 1)
        self.s.add_look(3, -4)
        self.assertEqual(self.s.consume_look(), (5, -3))
        self.assertEqual(self.s.consume_look(), (0.0, 0.0))


class TestLookAngles(unittest.TestCase):
    def test_signs(self):
        d_yaw, _ = look_angles(10, 0, 0.1)
        self.assertLess(d_yaw, 0.0)                    # mouse right -> turn right
        self.assertAlmostEqual(d_yaw, -math.radians(1.0))
        _, d_pitch = look_angles(0, 10, 0.1)
        self.assertGreater(d_pitch, 0.0)               # mouse up -> look up
        self.assertLess(look_angles(0, 10, 0.1, invert_pitch=True)[1], 0.0)


class TestMouseLook(unittest.TestCase):
    RECT = (0, 0, 800, 600)   # center (400, 300), warp limit 60 px

    def test_relative_motion(self):
        m = MouseLook()
        self.assertEqual(m.motion(400, 300), (0.0, 0.0))   # first event: begin
        self.assertEqual(m.motion(410, 295), (10.0, -5.0))
        self.assertEqual(m.motion(411, 295), (1.0, 0.0))

    def test_no_warp_near_center(self):
        m = MouseLook()
        m.begin(400, 300)
        m.motion(450, 320)
        self.assertIsNone(m.warp_request(self.RECT))

    def test_warp_event_causes_no_jump(self):
        m = MouseLook()
        m.begin(400, 300)
        total = [0.0, 0.0]

        def feed(x, y):
            dx, dy = m.motion(x, y)
            total[0] += dx
            total[1] += dy

        feed(560, 300)                            # +160 px, beyond the limit
        self.assertEqual(m.warp_request(self.RECT), (400, 300))
        self.assertIsNone(m.warp_request(self.RECT))   # already pending
        feed(565, 300)                            # stale pre-warp event: +5
        feed(403, 300)                            # post-warp event: +3 relative to center
        feed(408, 300)                            # normal: +5
        self.assertEqual(total, [173.0, 0.0])
        self.assertIsNone(m.warp_target)

    def test_edge_forces_warp_while_pending(self):
        m = MouseLook()
        m.begin(400, 300)
        m.motion(480, 300)
        self.assertEqual(m.warp_request(self.RECT), (400, 300))
        m.motion(790, 300)                        # fast stale motion to the edge
        self.assertEqual(m.warp_request(self.RECT), (400, 300))

    def test_outside_region_forces_warp(self):
        m = MouseLook()
        m.begin(400, 300)
        m.motion(480, 300)
        m.warp_request(self.RECT)
        m.motion(900, 300)                        # beyond the region
        self.assertEqual(m.warp_request(self.RECT), (400, 300))

    def test_warp_that_never_happens(self):
        m = MouseLook()
        m.begin(400, 300)
        m.motion(700, 300)
        self.assertIsNotNone(m.warp_request(self.RECT))
        x = 700
        for _ in range(MouseLook.MAX_STALE_EVENTS + 2):
            x += 1
            self.assertEqual(m.motion(x, 300), (1.0, 0.0))
        self.assertIsNone(m.warp_target)

    def test_reset(self):
        m = MouseLook()
        m.begin(1, 1)
        m.reset()
        self.assertFalse(m.active)
        self.assertEqual(m.motion(500, 500), (0.0, 0.0))  # re-begins without jump


class TestRouting(unittest.TestCase):
    def test_classify(self):
        window = (0, 0, 1000, 800)
        sidebar = (800, 0, 200, 800)   # overlapping the WINDOW region
        self.assertEqual(classify_point(100, 100, window, [sidebar]), "OWN")
        self.assertEqual(classify_point(900, 100, window, [sidebar]), "PASS")
        self.assertEqual(classify_point(1000, 100, window, [sidebar]), "OUTSIDE")
        self.assertEqual(classify_point(-1, 100, window, []), "OUTSIDE")
        self.assertEqual(classify_point(10, 10, None, []), "OUTSIDE")

    def test_point_in_rect_half_open(self):
        self.assertTrue(winput.point_in_rect(0, 0, (0, 0, 10, 10)))
        self.assertFalse(winput.point_in_rect(10, 5, (0, 0, 10, 10)))


if __name__ == "__main__":
    unittest.main()
