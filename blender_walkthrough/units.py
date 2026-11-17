"""Conversion between real-world meters and Blender world units.

The add-on UI always stores and shows values in meters. Physics and player
code work exclusively in world units, so every length/speed handed to them
must go through this module.

Blender's ``scene.unit_settings.scale_length`` is "meters per world unit"
(1 BU displayed as ``scale_length`` meters), therefore::

    world_per_meter = 1 / scale_length

No bpy import: ``scene`` is duck-typed (needs ``unit_settings.scale_length``)
so this module is testable with simple stand-in objects.
"""
from __future__ import annotations

import math

# Accepted range for scale_length. Outside of it we fall back to 1.0.
_MIN_SCALE = 1e-6
_MAX_SCALE = 1e6


def _scale_length(scene):
    unit_settings = getattr(scene, "unit_settings", None)
    if unit_settings is None:
        return None
    return getattr(unit_settings, "scale_length", None)


def world_per_meter(scene) -> tuple[float, str | None]:
    """Return (world units per meter, warning or None).

    Invalid scale (missing, <= 0, NaN/inf or absurd magnitude) falls back to
    1.0 with a warning text suitable for the UI.
    """
    scale = _scale_length(scene)
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        return 1.0, "Unit scale unavailable; assuming 1 BU = 1 m"
    if not math.isfinite(scale) or scale <= 0.0:
        return 1.0, f"Invalid unit scale ({scale}); assuming 1 BU = 1 m"
    if scale < _MIN_SCALE or scale > _MAX_SCALE:
        return 1.0, f"Unit scale {scale:g} out of range; assuming 1 BU = 1 m"
    return 1.0 / scale, None


def meters_to_world(value_m: float, scene) -> float:
    """Convert a length (or speed/acceleration) in meters to world units."""
    factor, _warning = world_per_meter(scene)
    return float(value_m) * factor


def world_to_meters(value_w: float, scene) -> float:
    """Inverse of :func:`meters_to_world` (for diagnostics display)."""
    factor, _warning = world_per_meter(scene)
    return float(value_w) / factor
