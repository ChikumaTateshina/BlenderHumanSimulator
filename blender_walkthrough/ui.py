"""3D View sidebar panel (category "Walkthrough")."""
import bpy

from . import session
from .session import SESSION


class WALKTHROUGH_PT_main(bpy.types.Panel):
    bl_label = "Walkthrough"
    bl_idname = "WALKTHROUGH_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Walkthrough"

    def draw(self, context):
        layout = self.layout
        state = SESSION.state
        settings = getattr(context.scene, "walkthrough_settings", None)

        box = layout.box()
        box.label(text=f"State: {state}", icon="PLAY" if state == session.RUNNING else "INFO")

        if state == session.IDLE:
            layout.operator("walkthrough.start", icon="PLAY")
        else:
            row = layout.row(align=True)
            if state == session.PAUSED:
                row.operator("walkthrough.resume", icon="PLAY")
            else:
                row.operator("walkthrough.pause", icon="PAUSE")
            row.operator("walkthrough.stop", icon="CANCEL")
            sub = layout.row()
            sub.enabled = state == session.PAUSED
            sub.operator("walkthrough.rebuild", icon="FILE_REFRESH")

        if settings is not None:
            col = layout.column(align=True)
            # Changes apply to the next session (values are converted at start).
            col.enabled = state == session.IDLE
            col.prop(settings, "eye_height")
            col.prop(settings, "walk_speed")
            col.prop(settings, "run_speed")
            col.prop(settings, "head_clearance")
            col.prop(settings, "mouse_sensitivity")
            col.prop(settings, "invert_pitch")
            col.prop(settings, "close_window_on_exit")


class WALKTHROUGH_PT_controls(bpy.types.Panel):
    bl_label = "Controls"
    bl_idname = "WALKTHROUGH_PT_controls"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Walkthrough"
    bl_parent_id = "WALKTHROUGH_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        col = self.layout.column(align=True)
        for text in ("WASD: move   Shift: run",
                     "Space: jump   C: crouch   Z: prone",
                     "Mouse: look (in walkthrough view)",
                     "P: pause / resume (frees the mouse)",
                     "Esc: exit",
                     "Edit in another window while paused,",
                     "then Rebuild Collisions."):
            col.label(text=text)


class WALKTHROUGH_PT_diagnostics(bpy.types.Panel):
    bl_label = "Diagnostics"
    bl_idname = "WALKTHROUGH_PT_diagnostics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Walkthrough"
    bl_parent_id = "WALKTHROUGH_PT_main"

    def draw(self, context):
        col = self.layout.column(align=True)
        try:
            lines = SESSION.diagnostics.lines()
        except Exception as exc:
            lines = [f"Diagnostics unavailable: {exc}"]
        for line in lines:
            icon = "ERROR" if line.startswith("Error") else "NONE"
            col.label(text=line, icon=icon)


classes = (WALKTHROUGH_PT_main, WALKTHROUGH_PT_controls, WALKTHROUGH_PT_diagnostics)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
