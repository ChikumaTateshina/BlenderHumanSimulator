"""Unit tests for blender_walkthrough.units (pure logic)."""
import math
import unittest

from blender_walkthrough import units


class _US:
    def __init__(self, scale):
        self.scale_length = scale


class _Scene:
    def __init__(self, scale):
        self.unit_settings = _US(scale)


class TestUnits(unittest.TestCase):
    def test_identity_scale(self):
        f, warn = units.world_per_meter(_Scene(1.0))
        self.assertEqual(f, 1.0)
        self.assertIsNone(warn)
        self.assertAlmostEqual(units.meters_to_world(1.6, _Scene(1.0)), 1.6)

    def test_centimeter_scene(self):
        # scale_length 0.01: 1 BU displays as 1 cm -> 1 m = 100 BU
        s = _Scene(0.01)
        self.assertAlmostEqual(units.world_per_meter(s)[0], 100.0)
        self.assertAlmostEqual(units.meters_to_world(1.6, s), 160.0)
        self.assertAlmostEqual(units.meters_to_world(3.0, s), 300.0)  # speeds scale too

    def test_large_scale(self):
        s = _Scene(2.0)
        self.assertAlmostEqual(units.meters_to_world(0.30, s), 0.15)

    def test_roundtrip(self):
        s = _Scene(0.3048)
        for v in (0.0, 0.2, 1.6, 9.81):
            self.assertAlmostEqual(units.world_to_meters(units.meters_to_world(v, s), s), v)

    def test_invalid_scales_fall_back(self):
        for bad in (0.0, -1.0, float("nan"), float("inf"), None, "x", 1e-9, 1e9):
            f, warn = units.world_per_meter(_Scene(bad))
            self.assertEqual(f, 1.0, bad)
            self.assertIsInstance(warn, str, bad)
            self.assertAlmostEqual(units.meters_to_world(1.6, _Scene(bad)), 1.6)

    def test_missing_unit_settings(self):
        f, warn = units.world_per_meter(object())
        self.assertEqual(f, 1.0)
        self.assertTrue(warn)

    def test_real_blender_scene(self):
        try:
            import bpy
        except ImportError:
            self.skipTest("bpy not available")
        scene = bpy.data.scenes[0]
        old = scene.unit_settings.scale_length
        try:
            scene.unit_settings.scale_length = 0.5
            self.assertAlmostEqual(units.meters_to_world(1.0, scene), 2.0)
        finally:
            scene.unit_settings.scale_length = old
        self.assertTrue(math.isfinite(units.world_per_meter(scene)[0]))


if __name__ == "__main__":
    unittest.main()
