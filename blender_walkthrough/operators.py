"""Operators: Start / Stop / Pause / Resume / Rebuild and the internal modal."""
import traceback

import bpy

from . import session
from .session import SESSION


class WALKTHROUGH_OT_start(bpy.types.Operator):
    """Open a dedicated window and walk from the 3D cursor in first person"""
    bl_idname = "walkthrough.start"
    bl_label = "Start Walkthrough"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        area = context.area
        return (SESSION.state == session.IDLE and area is not None
                and area.type == "VIEW_3D" and context.scene is not None)

    def execute(self, context):
        ok, msg, warnings = SESSION.start(context)
        for w in warnings:
            self.report({"WARNING"}, w)
        if not ok:
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class WALKTHROUGH_OT_stop(bpy.types.Operator):
    """Stop the walkthrough and release all resources"""
    bl_idname = "walkthrough.stop"
    bl_label = "Stop Walkthrough"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return SESSION.is_active

    def execute(self, context):
        SESSION.stop(reason="Stop button")
        return {"FINISHED"}


class WALKTHROUGH_OT_pause(bpy.types.Operator):
    """Pause simulation (time, movement, gravity and input stop)"""
    bl_idname = "walkthrough.pause"
    bl_label = "Pause"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return SESSION.state == session.RUNNING

    def execute(self, context):
        return {"FINISHED"} if SESSION.pause() else {"CANCELLED"}


class WALKTHROUGH_OT_resume(bpy.types.Operator):
    """Resume the paused simulation"""
    bl_idname = "walkthrough.resume"
    bl_label = "Resume"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return SESSION.state == session.PAUSED

    def execute(self, context):
        return {"FINISHED"} if SESSION.resume() else {"CANCELLED"}


class WALKTHROUGH_OT_rebuild(bpy.types.Operator):
    """Re-collect visible meshes and rebuild collision data (only while paused)"""
    bl_idname = "walkthrough.rebuild"
    bl_label = "Rebuild Collisions"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return SESSION.state == session.PAUSED

    def execute(self, context):
        ok, msg = SESSION.rebuild(context)
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        return {"FINISHED"} if ok else {"CANCELLED"}


class WALKTHROUGH_OT_modal(bpy.types.Operator):
    """Internal: input capture of the dedicated walkthrough area"""
    bl_idname = "walkthrough.modal"
    bl_label = "Walkthrough Input"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return SESSION.state == session.PREPARING

    def invoke(self, context, event):
        self._token = SESSION.token
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        try:
            return SESSION.handle_event(context, event, self._token)
        except Exception as exc:
            traceback.print_exc()
            SESSION.fail(f"{type(exc).__name__}: {exc}")
            return {"CANCELLED"}

    def cancel(self, context):
        try:
            SESSION.on_modal_cancel(getattr(self, "_token", -1))
        except Exception:
            traceback.print_exc()


classes = (
    WALKTHROUGH_OT_start,
    WALKTHROUGH_OT_stop,
    WALKTHROUGH_OT_pause,
    WALKTHROUGH_OT_resume,
    WALKTHROUGH_OT_rebuild,
    WALKTHROUGH_OT_modal,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
