"""Scene settings for the walkthrough (``scene.walkthrough_settings``).

All lengths/speeds are stored and displayed in **meters** (m, m/s) regardless
of the scene unit scale. We deliberately do not use ``unit='LENGTH'`` /
``'VELOCITY'``: Blender would interpret the stored value as world units and
display it multiplied by ``scale_length``. Conversion to world units happens
in ``units.py`` when a session starts.
"""
import bpy
from bpy.props import BoolProperty, FloatProperty, PointerProperty


class WALKTHROUGH_PG_settings(bpy.types.PropertyGroup):
    eye_height: FloatProperty(
        name="Eye Height (m)",
        description="Standing eye height above the feet, in meters",
        default=1.60, min=0.30, soft_max=2.50, max=10.0, precision=2, step=1,
    )
    walk_speed: FloatProperty(
        name="Walk Speed (m/s)",
        description="Walking speed in meters per second",
        default=3.0, min=0.1, soft_max=10.0, max=100.0, precision=2,
    )
    run_speed: FloatProperty(
        name="Run Speed (m/s)",
        description="Speed while Shift is held, in meters per second",
        default=5.0, min=0.1, soft_max=20.0, max=200.0, precision=2,
    )
    mouse_sensitivity: FloatProperty(
        name="Mouse Sensitivity",
        description="View rotation in degrees per pixel of mouse movement",
        default=0.05, min=0.01, soft_max=1.0, max=5.0, precision=3, step=1,
    )
    head_clearance: FloatProperty(
        name="Head Clearance (m)",
        description="Distance from the eye to the top of the collision capsule, in meters",
        default=0.10, min=0.0, soft_max=0.5, max=2.0, precision=2, step=1,
    )
    invert_pitch: BoolProperty(
        name="Invert Mouse Y",
        description="Invert vertical mouse look",
        default=False,
    )
    close_window_on_exit: BoolProperty(
        name="Close Window on Exit",
        description="Close the dedicated walkthrough window when the session ends",
        default=True,
    )


classes = (WALKTHROUGH_PG_settings,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.walkthrough_settings = PointerProperty(type=WALKTHROUGH_PG_settings)


def unregister():
    if hasattr(bpy.types.Scene, "walkthrough_settings"):
        del bpy.types.Scene.walkthrough_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
