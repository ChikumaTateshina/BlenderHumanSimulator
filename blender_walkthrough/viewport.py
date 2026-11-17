"""View state snapshot / restore and first-person view application.

Only touches SpaceView3D / RegionView3D display state (never scene data).
"""
from __future__ import annotations

from mathutils import Quaternion, Vector

# RegionView3D properties saved and restored.
_RV3D_PROPS = ("view_perspective", "view_location", "view_rotation",
               "view_distance", "lock_rotation", "use_box_clip")
# SpaceView3D properties saved and restored (scalars only).
_SPACE_PROPS = ("clip_start", "clip_end", "lens", "show_gizmo",
                "show_region_header", "show_region_tool_header",
                "show_region_toolbar", "show_region_ui", "show_region_hud",
                "show_region_asset_shelf")


def _copy_value(v):
    if isinstance(v, (Vector, Quaternion)):
        return v.copy()
    if hasattr(v, "__len__") and not isinstance(v, str):
        try:
            return tuple(v)
        except TypeError:
            return v
    return v


def rna_scalar_values(struct) -> dict:
    """Read all writable non-pointer RNA properties of ``struct``."""
    out = {}
    if struct is None:
        return out
    for prop in struct.bl_rna.properties:
        ident = prop.identifier
        if ident == "rna_type" or prop.is_readonly:
            continue
        if prop.type in {"POINTER", "COLLECTION"}:
            continue
        try:
            out[ident] = _copy_value(getattr(struct, ident))
        except Exception:
            pass
    return out


def rna_apply_values(struct, values: dict) -> None:
    """Write values captured by :func:`rna_scalar_values`, ignoring failures."""
    if struct is None:
        return
    for ident, value in values.items():
        try:
            # Skip unchanged values: writes trigger RNA updates (e.g. region
            # visibility flags re-initialise the area).
            if _same(getattr(struct, ident), value):
                continue
            setattr(struct, ident, value)
        except Exception:
            pass


def _same(current, value) -> bool:
    try:
        if isinstance(value, tuple) or isinstance(current, (Vector, Quaternion)):
            return tuple(current) == tuple(value)
        return current == value
    except Exception:
        return False


def copy_display_settings(src_space, dst_space) -> None:
    """Copy shading (Solid/Material/Rendered + options) and overlay settings."""
    for attr in ("shading", "overlay"):
        src = getattr(src_space, attr, None)
        dst = getattr(dst_space, attr, None)
        if src is None or dst is None:
            continue
        values = rna_scalar_values(src)
        # Set the shading type first so dependent options are accepted.
        if "type" in values:
            try:
                dst.type = values.pop("type")
            except Exception:
                pass
        rna_apply_values(dst, values)
    for ident in ("clip_start", "clip_end", "lens"):
        try:
            setattr(dst_space, ident, getattr(src_space, ident))
        except Exception:
            pass


class ViewStateSnapshot:
    """Saved display/view state of one VIEW_3D area."""

    def __init__(self) -> None:
        self.rv3d: dict = {}
        self.space: dict = {}
        self.shading: dict = {}
        self.overlay: dict = {}

    @classmethod
    def capture(cls, space, rv3d) -> "ViewStateSnapshot":
        snap = cls()
        if rv3d is not None:
            for ident in _RV3D_PROPS:
                try:
                    snap.rv3d[ident] = _copy_value(getattr(rv3d, ident))
                except Exception:
                    pass
        if space is not None:
            for ident in _SPACE_PROPS:
                if hasattr(space, ident):
                    try:
                        snap.space[ident] = _copy_value(getattr(space, ident))
                    except Exception:
                        pass
            snap.shading = rna_scalar_values(getattr(space, "shading", None))
            snap.overlay = rna_scalar_values(getattr(space, "overlay", None))
        return snap

    def restore(self, space, rv3d) -> None:
        """Restore everything captured. Each property is best-effort."""
        if space is not None:
            shading = getattr(space, "shading", None)
            if shading is not None and self.shading:
                values = dict(self.shading)
                if "type" in values:
                    try:
                        shading.type = values.pop("type")
                    except Exception:
                        pass
                rna_apply_values(shading, values)
            rna_apply_values(getattr(space, "overlay", None), self.overlay)
            rna_apply_values(space, self.space)
        if rv3d is not None:
            # Perspective first, then location/rotation/distance.
            rna_apply_values(rv3d, self.rv3d)


def view_forward_rotation(space, rv3d, scene) -> Quaternion:
    """Rotation the user was looking with in the source view.

    In camera view ``view_rotation`` is not what is displayed, so use the
    active camera orientation instead.
    """
    if rv3d is None:
        return Quaternion()
    try:
        if rv3d.view_perspective == "CAMERA":
            cam = getattr(space, "camera", None) or getattr(scene, "camera", None)
            if cam is not None:
                return cam.matrix_world.to_quaternion()
    except Exception:
        pass
    return rv3d.view_rotation.copy()


def apply_first_person(rv3d, eye: Vector, rotation: Quaternion, distance: float) -> None:
    """Place the perspective view so the viewpoint is exactly at ``eye``.

    Blender's view origin is ``view_location + view_rotation @ (0, 0, view_distance)``,
    so view_location is set behind the eye along the view axis.
    """
    if rv3d.view_perspective != "PERSP":
        rv3d.view_perspective = "PERSP"
    rv3d.view_rotation = rotation
    rv3d.view_distance = distance
    rv3d.view_location = eye - rotation @ Vector((0.0, 0.0, distance))
