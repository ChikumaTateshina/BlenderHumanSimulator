"""Collision world: world-space triangle snapshot, BVH per object, uniform grid.

bpy is imported lazily inside CollisionWorld.build only, so from_triangles()
and all queries work outside Blender (mathutils is still required).
"""
from __future__ import annotations

import math
import time

from mathutils import Vector
from mathutils.bvhtree import BVHTree

_EPS = 1e-6
_MAX_CELLS_PER_OBJECT = 4096   # larger objects go to the "always check" list
_TARGET_CELLS_PER_AXIS = 32
_NON_MESH_REPORTED = {"CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD",
                      "VOLUME", "GPENCIL", "GREASEPENCIL"}


class CollisionBuildError(Exception):
    """Raised when the collision world cannot be built."""


class CollisionObject:
    """World-space snapshot of one object."""

    __slots__ = ("name", "bvh", "vertices", "triangles", "aabb_min", "aabb_max")

    def __init__(self, name, vertices, triangles):
        self.name: str = name
        self.vertices: list[Vector] = [Vector(v) for v in vertices]
        self.triangles: list[tuple[int, int, int]] = [tuple(t) for t in triangles]
        xs = [v.x for v in self.vertices]
        ys = [v.y for v in self.vertices]
        zs = [v.z for v in self.vertices]
        self.aabb_min = Vector((min(xs), min(ys), min(zs)))
        self.aabb_max = Vector((max(xs), max(ys), max(zs)))
        self.bvh = BVHTree.FromPolygons(self.vertices, self.triangles, all_triangles=True)


class CollisionWorld:
    def __init__(self) -> None:
        self.objects: list[CollisionObject] = []
        self.stats: dict = self._empty_stats()
        self._grid: dict = {}
        self._large: list[int] = []     # indices of objects not stored in the grid
        self._cell: float = 1.0
        self._origin = Vector((0.0, 0.0, 0.0))

    @staticmethod
    def _empty_stats() -> dict:
        return {"object_count": 0, "triangle_count": 0, "build_time_ms": 0.0,
                "skipped": [], "warnings": []}

    # ------------------------------------------------------------ construction
    @classmethod
    def from_triangles(cls, items) -> "CollisionWorld":
        """items: [(name, world-space vertices, triangles)]. No bpy needed."""
        world = cls()
        t0 = time.perf_counter()
        try:
            for name, verts, tris in items:
                if len(tris) == 0:
                    world.stats["skipped"].append((name, "no triangles"))
                    continue
                world.objects.append(CollisionObject(name, verts, tris))
            world._finalize(t0)
        except Exception as exc:
            world.free()
            raise CollisionBuildError(str(exc)) from exc
        return world

    @classmethod
    def build(cls, context, view_layer=None, depsgraph=None) -> "CollisionWorld":
        import bpy  # noqa: F401  (lazy: keeps the module importable without bpy)

        world = cls()
        t0 = time.perf_counter()
        try:
            if view_layer is None:
                view_layer = context.view_layer
            if depsgraph is None:
                depsgraph = context.evaluated_depsgraph_get()

            # Objects that produce instances (geometry nodes, particles, collection instances).
            instancers = set()
            for inst in depsgraph.object_instances:
                if inst.is_instance and inst.parent is not None:
                    instancers.add(inst.parent.original.name)

            for obj in view_layer.objects:
                if not obj.visible_get(view_layer=view_layer):
                    continue
                if obj.type != "MESH":
                    if obj.type in _NON_MESH_REPORTED:
                        world.stats["skipped"].append((obj.name, f"non-mesh object type {obj.type}"))
                    continue
                if obj.name in instancers or getattr(obj, "is_instancer", False):
                    world.stats["skipped"].append(
                        (obj.name, "instances are not supported (base mesh only)"))
                co = cls._extract(obj, depsgraph)
                if co is None:
                    world.stats["skipped"].append((obj.name, "no triangles"))
                    continue
                world.objects.append(co)
            world._finalize(t0)
        except CollisionBuildError:
            world.free()
            raise
        except Exception as exc:
            world.free()
            raise CollisionBuildError(f"{type(exc).__name__}: {exc}") from exc
        return world

    @staticmethod
    def _extract(obj, depsgraph):
        """Evaluate obj and return a CollisionObject (or None if no triangles)."""
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = None
        try:
            mesh = eval_obj.to_mesh()
            if mesh is None:
                return None
            mesh.calc_loop_triangles()
            if len(mesh.loop_triangles) == 0:
                return None
            mw = eval_obj.matrix_world.copy()
            verts = [mw @ v.co for v in mesh.vertices]
            tris = [tuple(t.vertices) for t in mesh.loop_triangles]
            return CollisionObject(obj.name, verts, tris)
        finally:
            eval_obj.to_mesh_clear()

    def _finalize(self, t0: float) -> None:
        """Build the grid index and fill stats."""
        self.stats["object_count"] = len(self.objects)
        self.stats["triangle_count"] = sum(len(o.triangles) for o in self.objects)
        if self.objects:
            lo = Vector((min(o.aabb_min.x for o in self.objects),
                         min(o.aabb_min.y for o in self.objects),
                         min(o.aabb_min.z for o in self.objects)))
            hi = Vector((max(o.aabb_max.x for o in self.objects),
                         max(o.aabb_max.y for o in self.objects),
                         max(o.aabb_max.z for o in self.objects)))
            extent = max(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z)
            if not math.isfinite(extent):
                raise CollisionBuildError("non-finite scene extent")
            self._origin = lo
            # Cell size: ~extent/32, but at least half the average object size.
            avg = sum((o.aabb_max - o.aabb_min).length for o in self.objects) / len(self.objects)
            self._cell = max(extent / _TARGET_CELLS_PER_AXIS, avg * 0.5, 1e-3)
            for i, o in enumerate(self.objects):
                rng = self._cell_range(o.aabb_min, o.aabb_max)
                n = (rng[3] - rng[0] + 1) * (rng[4] - rng[1] + 1) * (rng[5] - rng[2] + 1)
                if n > _MAX_CELLS_PER_OBJECT:
                    self._large.append(i)
                    continue
                for x in range(rng[0], rng[3] + 1):
                    for y in range(rng[1], rng[4] + 1):
                        for z in range(rng[2], rng[5] + 1):
                            self._grid.setdefault((x, y, z), []).append(i)
        self.stats["build_time_ms"] = (time.perf_counter() - t0) * 1000.0

    # ------------------------------------------------------------------ queries
    def _cell_range(self, mn, mx):
        c, o = self._cell, self._origin
        return (int(math.floor((mn.x - o.x) / c)), int(math.floor((mn.y - o.y) / c)),
                int(math.floor((mn.z - o.z) / c)), int(math.floor((mx.x - o.x) / c)),
                int(math.floor((mx.y - o.y) / c)), int(math.floor((mx.z - o.z) / c)))

    def query_objects(self, aabb_min: Vector, aabb_max: Vector) -> list[CollisionObject]:
        if not self.objects:
            return []
        rng = self._cell_range(aabb_min, aabb_max)
        ncells = (rng[3] - rng[0] + 1) * (rng[4] - rng[1] + 1) * (rng[5] - rng[2] + 1)
        if ncells > _MAX_CELLS_PER_OBJECT:
            cand = range(len(self.objects))      # huge query: scan all objects
        else:
            found = set(self._large)
            grid = self._grid
            for x in range(rng[0], rng[3] + 1):
                for y in range(rng[1], rng[4] + 1):
                    for z in range(rng[2], rng[5] + 1):
                        lst = grid.get((x, y, z))
                        if lst:
                            found.update(lst)
            cand = sorted(found)
        out = []
        for i in cand:
            o = self.objects[i]
            a, b = o.aabb_min, o.aabb_max
            if (a.x <= aabb_max.x and b.x >= aabb_min.x and a.y <= aabb_max.y and
                    b.y >= aabb_min.y and a.z <= aabb_max.z and b.z >= aabb_min.z):
                out.append(o)
        return out

    def gather_triangles(self, aabb_min: Vector, aabb_max: Vector) -> list[tuple[Vector, Vector, Vector]]:
        """Triangles whose AABB overlaps the query AABB (no duplicates)."""
        objs = self.query_objects(aabb_min, aabb_max)
        if not objs:
            return []
        center = (aabb_min + aabb_max) * 0.5
        radius = (aabb_max - aabb_min).length * 0.5 + _EPS
        x0, y0, z0 = aabb_min.x, aabb_min.y, aabb_min.z
        x1, y1, z1 = aabb_max.x, aabb_max.y, aabb_max.z
        out = []
        for o in objs:
            verts, tris = o.vertices, o.triangles
            seen = set()
            for hit in o.bvh.find_nearest_range(center, radius):
                idx = hit[2]
                if idx in seen:
                    continue
                seen.add(idx)
                i, j, k = tris[idx]
                a, b, c = verts[i], verts[j], verts[k]
                if (max(a.x, b.x, c.x) < x0 or min(a.x, b.x, c.x) > x1 or
                        max(a.y, b.y, c.y) < y0 or min(a.y, b.y, c.y) > y1 or
                        max(a.z, b.z, c.z) < z0 or min(a.z, b.z, c.z) > z1):
                    continue
                out.append((a, b, c))
        return out

    def ray_cast(self, origin: Vector, direction: Vector, distance: float):
        """Nearest hit as (location, normal, distance) or None."""
        d = Vector(direction)
        if d.length_squared == 0.0 or distance <= 0.0:
            return None
        d.normalize()
        end = origin + d * distance
        mn = Vector((min(origin.x, end.x), min(origin.y, end.y), min(origin.z, end.z)))
        mx = Vector((max(origin.x, end.x), max(origin.y, end.y), max(origin.z, end.z)))
        best = None
        for o in self.query_objects(mn, mx):
            loc, nor, _idx, dist = o.bvh.ray_cast(origin, d, distance)
            if loc is not None and (best is None or dist < best[2]):
                best = (loc, nor.normalized(), dist)
        return best

    def free(self) -> None:
        """Release references. Idempotent."""
        self.objects = []
        self._grid = {}
        self._large = []
        self.stats = self._empty_stats()
