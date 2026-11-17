"""Diagnostics values shown in the UI (no bpy dependency)."""
from __future__ import annotations


class Diagnostics:
    """Plain holder of runtime diagnostic values."""

    def __init__(self) -> None:
        self.object_count: int = 0
        self.triangle_count: int = 0
        self.build_time_ms: float = 0.0
        self.candidate_triangles: int = 0
        self.update_time_ms: float = 0.0
        self.grounded: bool = False
        self.state: str = "IDLE"
        self.last_error: str = ""
        self.warnings: list[str] = []

    def update_from_world(self, world) -> None:
        """Copy build statistics from a CollisionWorld (duck-typed via .stats)."""
        stats = getattr(world, "stats", None) or {}
        self.object_count = int(stats.get("object_count", 0))
        self.triangle_count = int(stats.get("triangle_count", 0))
        self.build_time_ms = float(stats.get("build_time_ms", 0.0))
        warnings = [str(w) for w in stats.get("warnings", [])]
        for name, reason in stats.get("skipped", []):
            warnings.append(f"Skipped {name}: {reason}")
        self.warnings = warnings

    def set_error(self, msg: str) -> None:
        self.last_error = str(msg)

    def lines(self) -> list[str]:
        """Short strings for UI display."""
        out = [
            f"State: {self.state}",
            f"Objects: {self.object_count}  Tris: {self.triangle_count}",
            f"Build: {self.build_time_ms:.1f} ms",
            f"Candidates: {self.candidate_triangles}  Update: {self.update_time_ms:.2f} ms",
            f"Grounded: {'yes' if self.grounded else 'no'}",
        ]
        if self.warnings:
            out.append(f"Warnings: {len(self.warnings)}")
            out.extend(self.warnings[:3])
        if self.last_error:
            out.append(f"Error: {self.last_error}")
        return out
