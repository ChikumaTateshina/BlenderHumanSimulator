"""Blender Walkthrough: first-person walkthrough of the scene in a dedicated window.

Start from the 3D View sidebar (N panel, "Walkthrough" tab). The player
stands on the 3D cursor; collisions use a runtime snapshot of visible meshes
and never modify user data.
"""

bl_info = {
    "name": "Walkthrough",
    "author": "ChikumaTateshina",
    "version": (0, 1, 1),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Walkthrough",
    "description": "First-person walkthrough with capsule collision in a dedicated window",
    "category": "3D View",
}

if "bpy" in locals():
    # Script reload (F8 / reload scripts): re-import submodules.
    import importlib
    for _name in ("units", "input", "viewport", "windowing", "session",
                  "properties", "operators", "ui"):
        if _name in locals():
            importlib.reload(locals()[_name])

import bpy  # noqa: E402,F401  (marks the module as loaded for reloads)

from . import operators, properties, session, ui  # noqa: E402

_modules = (properties, operators, ui)


def register():
    for mod in _modules:
        mod.register()
    session.register()


def unregister():
    # Stop any active session first (timer, cursor, window, collision data),
    # before the operator classes it depends on disappear.
    session.unregister()
    for mod in reversed(_modules):
        try:
            mod.unregister()
        except Exception as exc:  # keep unregistering the rest
            print(f"[Walkthrough] unregister {mod.__name__}: {exc}")
