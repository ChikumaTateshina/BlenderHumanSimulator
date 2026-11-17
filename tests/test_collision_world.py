"""Tests for collision_world / diagnostics (pure tests + bpy tests when run in Blender)."""
import random
import unittest

from mathutils import Vector, geometry

from blender_walkthrough import collision_world, diagnostics
from blender_walkthrough.collision_world import CollisionWorld, CollisionBuildError

try:
    import bpy
except ImportError:  # pragma: no cover
    bpy = None


def box(mn, mx):
    """Axis-aligned box: (vertices, triangles)."""
    x0, y0, z0 = mn
    x1, y1, z1 = mx
    v = [Vector(p) for p in [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]]
    q = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    t = []
    for a, b, c, d in q:
        t += [(a, b, c), (a, c, d)]
    return v, t


def tri_overlaps(tri, mn, mx):
    for ax in range(3):
        if max(p[ax] for p in tri) < mn[ax] or min(p[ax] for p in tri) > mx[ax]:
            return False
    return True


class TestPure(unittest.TestCase):
    def setUp(self):
        v1, t1 = box((-1, -1, -1), (1, 1, 1))
        v2, t2 = box((10, 10, 0), (12, 12, 2))
        self.world = CollisionWorld.from_triangles([("A", v1, t1), ("B", v2, t2), ("E", v1, [])])

    def test_stats(self):
        s = self.world.stats
        self.assertEqual(s["object_count"], 2)
        self.assertEqual(s["triangle_count"], 24)
        self.assertEqual(s["skipped"], [("E", "no triangles")])

    def test_query_objects(self):
        r = self.world.query_objects(Vector((0, 0, 0)), Vector((0.5, 0.5, 0.5)))
        self.assertEqual([o.name for o in r], ["A"])
        r = self.world.query_objects(Vector((-100, -100, -100)), Vector((100, 100, 100)))
        self.assertEqual(sorted(o.name for o in r), ["A", "B"])
        self.assertEqual(self.world.query_objects(Vector((50, 50, 50)), Vector((51, 51, 51))), [])

    def test_gather_vs_brute_force(self):
        rnd = random.Random(1)
        items = []
        for i in range(6):
            o = Vector([rnd.uniform(-5, 5) for _ in range(3)])
            v, t = box(o, o + Vector((rnd.uniform(0.5, 6), rnd.uniform(0.5, 6), rnd.uniform(0.5, 6))))
            items.append((f"o{i}", v, t))
        w = CollisionWorld.from_triangles(items)
        alltris = [tuple(v[i] for i in tri) for _n, v, ts in items for tri in ts]
        for _ in range(100):
            c = Vector([rnd.uniform(-6, 8) for _ in range(3)])
            h = Vector([rnd.uniform(0.05, 1.5) for _ in range(3)])
            mn, mx = c - h, c + h
            got = w.gather_triangles(mn, mx)
            radius = (mx - mn).length * 0.5 + 1e-6
            # AABB overlap (upper bound) and "within circumscribed sphere" (exact criterion)
            exp = [t for t in alltris if tri_overlaps(t, mn, mx)
                   and (geometry.closest_point_on_tri(c, *t) - c).length <= radius]
            key = lambda t: tuple(round(x, 6) for p in t for x in p)
            self.assertEqual(sorted(map(key, got)), sorted(map(key, exp)))
            # every triangle with a vertex inside the box must be returned
            inside = [t for t in alltris if any(all(mn[a] <= p[a] <= mx[a] for a in range(3)) for p in t)]
            self.assertTrue(set(map(key, inside)) <= set(map(key, got)))

    def test_ray_cast(self):
        hit = self.world.ray_cast(Vector((-5, 0.2, 0.1)), Vector((1, 0, 0)), 100.0)
        self.assertIsNotNone(hit)
        loc, nor, dist = hit
        self.assertAlmostEqual(loc.x, -1.0, places=5)
        self.assertAlmostEqual(dist, 4.0, places=5)
        self.assertAlmostEqual(nor.length, 1.0, places=5)
        self.assertIsNone(self.world.ray_cast(Vector((-5, 0.2, 0.1)), Vector((1, 0, 0)), 2.0))
        self.assertIsNone(self.world.ray_cast(Vector((-5, 5, 5)), Vector((1, 0, 0)), 100.0))
        # nearest among several objects
        hit = self.world.ray_cast(Vector((-5, -4.5, 0.1)), Vector((1, 1, 0)).normalized(), 100.0)
        self.assertIsNotNone(hit)

    def test_nearest_of_two(self):
        v, t = box((5, -1, -1), (6, 1, 1))
        w = CollisionWorld.from_triangles([("far", *box((20, -1, -1), (21, 1, 1))), ("near", v, t)])
        hit = w.ray_cast(Vector((0, 0, 0)), Vector((1, 0, 0)), 100)
        self.assertAlmostEqual(hit[2], 5.0, places=5)

    def test_free_idempotent(self):
        self.world.free()
        self.world.free()
        self.assertEqual(self.world.query_objects(Vector((-9, -9, -9)), Vector((9, 9, 9))), [])
        self.assertEqual(self.world.gather_triangles(Vector((-9, -9, -9)), Vector((9, 9, 9))), [])
        self.assertIsNone(self.world.ray_cast(Vector((0, 0, 0)), Vector((1, 0, 0)), 5))
        self.assertEqual(self.world.stats["object_count"], 0)

    def test_bad_input_raises(self):
        with self.assertRaises(CollisionBuildError):
            CollisionWorld.from_triangles([("bad", [Vector((0, 0, 0))], [(0, 1, 2)])])

    def test_empty_world(self):
        w = CollisionWorld.from_triangles([])
        self.assertEqual(w.gather_triangles(Vector((0, 0, 0)), Vector((1, 1, 1))), [])


class TestDiagnostics(unittest.TestCase):
    def test_basic(self):
        w = CollisionWorld.from_triangles([("A", *box((0, 0, 0), (1, 1, 1))), ("E", [], [])])
        d = diagnostics.Diagnostics()
        d.update_from_world(w)
        self.assertEqual((d.object_count, d.triangle_count), (1, 12))
        self.assertTrue(any("E" in x for x in d.warnings))
        d.set_error("boom")
        lines = d.lines()
        self.assertTrue(all(isinstance(x, str) for x in lines))
        self.assertTrue(any("boom" in x for x in lines))


@unittest.skipIf(bpy is None, "requires Blender")
class TestBuild(unittest.TestCase):
    def setUp(self):
        self.scene = bpy.data.scenes.new("WT_TestScene")
        self.vl = self.scene.view_layers[0]
        self.created_objs = []
        self.created_meshes = []

    def tearDown(self):
        for o in list(self.scene.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for o in self.created_objs:
            if o.name in bpy.data.objects:
                bpy.data.objects.remove(o, do_unlink=True)
        for m in self.created_meshes:
            if m.name in bpy.data.meshes:
                bpy.data.meshes.remove(m)
        for c in list(bpy.data.collections):
            if c.name.startswith("WT_"):
                bpy.data.collections.remove(c)
        for c in list(bpy.data.curves):
            if c.name.startswith("WT_"):
                bpy.data.curves.remove(c)
        bpy.data.scenes.remove(self.scene)

    # helpers
    def add_box(self, name, mn=(-1, -1, -1), mx=(1, 1, 1), collection=None):
        v, t = box(mn, mx)
        me = bpy.data.meshes.new("WT_" + name)
        me.from_pydata([tuple(p) for p in v], [], [tuple(x) for x in t])
        me.update()
        ob = bpy.data.objects.new(name, me)
        (collection or self.scene.collection).objects.link(ob)
        self.created_meshes.append(me)
        return ob

    def build(self):
        self.vl.update()
        return CollisionWorld.build(bpy.context, view_layer=self.vl, depsgraph=self.vl.depsgraph)

    def names(self, w):
        return sorted(o.name for o in w.objects)

    def test_visible_mesh_collected(self):
        self.add_box("A")
        w = self.build()
        self.assertEqual(self.names(w), ["A"])
        self.assertEqual(w.stats["triangle_count"], 12)
        self.assertGreaterEqual(w.stats["build_time_ms"], 0.0)
        o = w.objects[0]
        self.assertAlmostEqual((o.aabb_min - Vector((-1, -1, -1))).length, 0.0, places=5)

    def test_hidden_excluded(self):
        a = self.add_box("A")
        b = self.add_box("B")
        c = self.add_box("C")
        b.hide_set(True, view_layer=self.vl)
        c.hide_viewport = True
        w = self.build()
        self.assertEqual(self.names(w), ["A"])

    def test_excluded_collection(self):
        col = bpy.data.collections.new("WT_Excl")
        self.scene.collection.children.link(col)
        self.add_box("A")
        self.add_box("B", collection=col)
        self.scene.view_layers[0].update()
        self.scene.view_layers[0].layer_collection.children["WT_Excl"].exclude = True
        w = self.build()
        self.assertEqual(self.names(w), ["A"])

    def test_non_mesh_skipped(self):
        self.add_box("A")
        cu = bpy.data.curves.new("WT_cu", "CURVE")
        cu.splines.new("POLY").points.add(2)
        co = bpy.data.objects.new("Curve", cu)
        self.scene.collection.objects.link(co)
        fo = bpy.data.curves.new("WT_txt", "FONT")
        to = bpy.data.objects.new("Text", fo)
        self.scene.collection.objects.link(to)
        w = self.build()
        self.assertEqual(self.names(w), ["A"])
        skipped = dict(w.stats["skipped"])
        self.assertIn("Curve", skipped)
        self.assertIn("Text", skipped)
        self.assertIn("CURVE", skipped["Curve"])

    def test_negative_and_nonuniform_scale(self):
        a = self.add_box("A", (0, 0, 0), (1, 1, 1))
        a.scale = (-2.0, 3.0, 0.5)
        a.location = (10, 0, 0)
        w = self.build()
        o = w.objects[0]
        self.assertAlmostEqual((o.aabb_min - Vector((8, 0, 0))).length, 0.0, places=5)
        self.assertAlmostEqual((o.aabb_max - Vector((10, 3, 0.5))).length, 0.0, places=5)
        # all world vertices equal matrix_world @ local
        mw = a.matrix_world
        exp = sorted(tuple(round(c, 4) for c in (mw @ v.co)) for v in a.data.vertices)
        got = sorted(tuple(round(c, 4) for c in v) for v in o.vertices)
        self.assertEqual(exp, got)
        # ray from outside hits the scaled face at x=8
        hit = w.ray_cast(Vector((0, 1, 0.25)), Vector((1, 0, 0)), 100)
        self.assertAlmostEqual(hit[0].x, 8.0, places=4)

    def test_modifier_applied(self):
        a = self.add_box("A", (0, 0, 0), (1, 1, 1))
        m = a.modifiers.new("arr", "ARRAY")
        m.count = 3
        m.use_relative_offset = False
        m.use_constant_offset = True
        m.constant_offset_displace = (5, 0, 0)
        w = self.build()
        o = w.objects[0]
        self.assertEqual(len(o.triangles), 36)
        self.assertAlmostEqual(o.aabb_max.x, 11.0, places=4)
        self.assertEqual(len(a.data.vertices), 8)  # source untouched

    def test_snapshot_independence_and_source_unchanged(self):
        a = self.add_box("A")
        mods_before = len(a.modifiers)
        w = self.build()
        # edit source mesh and object transform after build
        for v in a.data.vertices:
            v.co.z += 10.0
        a.location.x = 50
        self.vl.update()
        o = w.objects[0]
        self.assertAlmostEqual(o.aabb_max.z, 1.0, places=5)
        self.assertAlmostEqual(o.aabb_max.x, 1.0, places=5)
        hit = w.ray_cast(Vector((-5, 0, 0)), Vector((1, 0, 0)), 100)
        self.assertAlmostEqual(hit[0].x, -1.0, places=5)
        # rebuild picks up new geometry
        w2 = self.build()
        self.assertAlmostEqual(w2.objects[0].aabb_max.z, 11.0, places=4)
        self.assertAlmostEqual(w2.objects[0].aabb_max.x, 51.0, places=4)
        # source integrity
        self.assertEqual(len(a.data.vertices), 8)
        self.assertEqual(len(a.modifiers), mods_before)
        self.assertIsNone(a.rigid_body)
        self.assertEqual(len(a.constraints), 0)
        self.assertIsNone(self.scene.rigidbody_world)
        self.assertEqual(sorted(o.name for o in self.scene.objects), ["A"])

    def test_empty_mesh_skipped(self):
        me = bpy.data.meshes.new("WT_empty")
        self.created_meshes.append(me)
        ob = bpy.data.objects.new("Empty", me)
        self.scene.collection.objects.link(ob)
        self.add_box("A")
        w = self.build()
        self.assertEqual(self.names(w), ["A"])
        self.assertIn(("Empty", "no triangles"), w.stats["skipped"])

    def test_build_error(self):
        with self.assertRaises(CollisionBuildError):
            CollisionWorld.build(bpy.context, view_layer=object(), depsgraph=self.vl.depsgraph)


if __name__ == "__main__":
    unittest.main()
