# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Express Tools actions: core headless, undoable CAD commands."""
from __future__ import annotations

import math
from typing import Optional, Sequence

from ezdxf.math import Vec2, Vec3
from core.commands import Command


# -----------------------------------------------------------------------------
# 1. BLOCKS: BURST (Explode with Attribute to Text Conversion)
# -----------------------------------------------------------------------------

class BurstCommand(Command):
    """BURST: Explodes block references, converting ATTRIBs to real TEXT/MTEXT.

    Unlike standard EXPLODE (which deletes attribute values and reverts to
    generic ATTDEF tag names), BURST preserves the current assigned text values,
    layers, colors, text styles, and geometric positions.
    """

    name = "BURST"

    def __init__(self, entities: Sequence) -> None:
        self.sources = [e for e in entities if getattr(e, "dxftype", lambda: "")() == "INSERT"]
        self._created_pieces: list[tuple[object, list[object]]] = []

    def do(self, document) -> None:
        space = self.space(document)
        self._created_pieces = []

        for insert in self.sources:
            if not insert.is_alive or insert.dxftype() != "INSERT":
                continue

            created: list[object] = []

            # 1. Convert actual attributes (ATTRIB) into plain TEXT or MTEXT
            for attrib in getattr(insert, "attribs", ()):
                txt_val = str(attrib.dxf.get("text", "") or "")
                if not txt_val:
                    continue

                dxfattribs = {
                    "layer": attrib.dxf.get("layer", insert.dxf.layer),
                    "color": attrib.dxf.get("color", 256),
                    "style": attrib.dxf.get("style", "STANDARD"),
                    "height": attrib.dxf.get("height", 2.5),
                    "rotation": attrib.dxf.get("rotation", 0.0),
                }

                # Multiline attribute or single-line
                if attrib.dxftype() == "MTEXT":
                    ent = space.add_mtext(txt_val, dxfattribs=dxfattribs)
                    ent.dxf.insert = attrib.dxf.insert
                else:
                    ent = space.add_text(txt_val, dxfattribs=dxfattribs)
                    ent.dxf.insert = attrib.dxf.insert
                    if attrib.dxf.hasattr("align_point"):
                        ent.dxf.align_point = attrib.dxf.align_point
                    if attrib.dxf.hasattr("halign"):
                        ent.dxf.halign = attrib.dxf.halign
                    if attrib.dxf.hasattr("valign"):
                        ent.dxf.valign = attrib.dxf.valign

                created.append(ent)

            # 2. Extract block geometry (lines, arcs, polylines etc.), skipping raw ATTDEFs
            for v in insert.virtual_entities():
                if v.dxftype() == "ATTDEF":
                    continue  # Replaced by converted ATTRIBs above
                space.add_entity(v)
                created.append(v)

            space.unlink_entity(insert)
            self._created_pieces.append((insert, created))

        document.dirty = True

    def undo(self, document) -> None:
        space = self.space(document)
        for original, created_ents in self._created_pieces:
            for c in created_ents:
                if c.is_alive:
                    space.unlink_entity(c)
                    document.doc.entitydb.delete_entity(c)
            space.add_entity(original)

        self._created_pieces = []
        document.dirty = True


def bcount_summary(document, entities: Optional[Sequence] = None) -> list[tuple[str, int]]:
    """BCOUNT: Count block insertions in the selection or entire drawing."""
    counts: dict[str, int] = {}
    source = entities if entities is not None else document.current_space()
    for e in source:
        if getattr(e, "dxftype", lambda: "")() == "INSERT":
            name = str(e.dxf.get("name", "") or "")
            if name:
                counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))


class BlockReplaceCommand(Command):
    """BLOCKREPLACE: Replace all occurrences of block A with block B."""

    name = "BLOCKREPLACE"

    def __init__(self, old_name: str, new_name: str) -> None:
        self.old_name = old_name
        self.new_name = new_name
        self._modified: list[object] = []

    def do(self, document) -> None:
        self._modified = []
        for space in [document.doc.modelspace()] + list(document.doc.layouts):
            for e in space:
                if e.dxftype() == "INSERT" and e.dxf.name.lower() == self.old_name.lower():
                    e.dxf.name = self.new_name
                    self._modified.append(e)
        document.dirty = True

    def undo(self, document) -> None:
        for e in self._modified:
            if e.is_alive:
                e.dxf.name = self.old_name
        self._modified = []
        document.dirty = True


# -----------------------------------------------------------------------------
# 2. TEXT: TXT2MTXT, TCASE, TCOUNT, TCIRCLE, TORIENT, TEXTFIT
# -----------------------------------------------------------------------------

class Txt2MtxtCommand(Command):
    """TXT2MTXT: Combines multiple single-line TEXT entities into one MTEXT."""

    name = "TXT2MTXT"

    def __init__(self, text_entities: Sequence) -> None:
        self.texts = [e for e in text_entities if getattr(e, "dxftype", lambda: "")() == "TEXT"]
        self._created_mtext: Optional[object] = None

    def do(self, document) -> None:
        if not self.texts:
            return
        space = self.space(document)

        # Sort top-to-bottom (Y descending), then left-to-right (X ascending)
        sorted_texts = sorted(
            self.texts,
            key=lambda t: (-float(t.dxf.insert[1]), float(t.dxf.insert[0]))
        )

        lines = [str(t.dxf.get("text", "") or "") for t in sorted_texts]
        combined_text = "\\P".join(lines)

        lead = sorted_texts[0]
        dxfattribs = {
            "layer": lead.dxf.layer,
            "color": lead.dxf.color,
            "style": lead.dxf.style,
            "char_height": lead.dxf.height,
            "rotation": lead.dxf.rotation,
        }

        self._created_mtext = space.add_mtext(combined_text, dxfattribs=dxfattribs)
        self._created_mtext.dxf.insert = lead.dxf.insert

        for t in self.texts:
            space.unlink_entity(t)

        document.dirty = True

    def undo(self, document) -> None:
        space = self.space(document)
        if self._created_mtext is not None and self._created_mtext.is_alive:
            space.unlink_entity(self._created_mtext)
            document.doc.entitydb.delete_entity(self._created_mtext)
            self._created_mtext = None

        for t in self.texts:
            space.add_entity(t)

        document.dirty = True


class TCaseCommand(Command):
    """TCASE: Change text case (UPPER, lower, Title, Sentence)."""

    name = "TCASE"

    def __init__(self, entities: Sequence, mode: str = "UPPER") -> None:
        self.entities = [e for e in entities if getattr(e, "dxftype", lambda: "")() in ("TEXT", "MTEXT")]
        self.mode = mode.upper()
        self._snapshots: list[tuple[object, str]] = []

    def _convert_case(self, text: str) -> str:
        if self.mode == "UPPER":
            return text.upper()
        if self.mode == "LOWER":
            return text.lower()
        if self.mode == "TITLE":
            return text.title()
        if self.mode == "SENTENCE":
            return text.capitalize()
        return text

    def do(self, document) -> None:
        self._snapshots = []
        for e in self.entities:
            if not e.is_alive:
                continue
            old_val = str(e.text if hasattr(e, "text") else getattr(e.dxf, "text", "") or "")
            self._snapshots.append((e, old_val))
            new_val = self._convert_case(old_val)
            if hasattr(e, "text"):
                e.text = new_val
            else:
                e.dxf.text = new_val
        document.dirty = True

    def undo(self, document) -> None:
        for e, old_val in self._snapshots:
            if e.is_alive:
                if hasattr(e, "text"):
                    e.text = old_val
                else:
                    e.dxf.text = old_val
        self._snapshots = []
        document.dirty = True


class TCountCommand(Command):
    """TCOUNT: Automatic sequential numbering of text objects."""

    name = "TCOUNT"

    def __init__(self, entities: Sequence, start: int = 1, step: int = 1,
                 order: str = "Y", placement: str = "OVERWRITE",
                 sort_mode: Optional[str] = None) -> None:
        self.entities = [e for e in entities if getattr(e, "dxftype", lambda: "")() in ("TEXT", "MTEXT")]
        self.start = start
        self.step = step
        self.order = (sort_mode or order).upper()
        self.placement = placement.upper()
        self._snapshots: list[tuple[object, str]] = []

    def do(self, document) -> None:
        if not self.entities:
            return

        # Sort order
        if self.order == "X":
            ordered = sorted(self.entities, key=lambda e: float(e.dxf.insert[0]))
        elif self.order == "Y":
            ordered = sorted(self.entities, key=lambda e: -float(e.dxf.insert[1]))
        else:
            ordered = list(self.entities)

        self._snapshots = []
        curr = self.start
        for e in ordered:
            if not e.is_alive:
                continue
            old_val = str(e.text if hasattr(e, "text") else getattr(e.dxf, "text", "") or "")
            self._snapshots.append((e, old_val))

            if self.placement == "PREFIX":
                new_val = f"{curr} {old_val}"
            elif self.placement == "SUFFIX":
                new_val = f"{old_val} {curr}"
            else:  # OVERWRITE
                new_val = str(curr)

            if hasattr(e, "text"):
                e.text = new_val
            else:
                e.dxf.text = new_val
            curr += self.step

        document.dirty = True

    def undo(self, document) -> None:
        for e, old_val in self._snapshots:
            if e.is_alive:
                if hasattr(e, "text"):
                    e.text = old_val
                else:
                    e.dxf.text = old_val
        self._snapshots = []
        document.dirty = True


class TCircleCommand(Command):
    """TCIRCLE: Enclose text objects with Circle, Slot, or Rectangle."""

    name = "TCIRCLE"

    def __init__(self, entities: Sequence, shape: str = "CIRCLE",
                 offset_factor: float = 0.35) -> None:
        self.entities = [e for e in entities if getattr(e, "dxftype", lambda: "")() in ("TEXT", "MTEXT")]
        self.shape = shape.upper()
        self.offset_factor = offset_factor
        self._created_enclosures: list[object] = []

    def do(self, document) -> None:
        space = self.space(document)
        self._created_enclosures = []

        for e in self.entities:
            if not e.is_alive:
                continue

            text_val = str(e.text if hasattr(e, "text") else getattr(e.dxf, "text", "") or "")
            h = float(getattr(e.dxf, "height", None) or getattr(e.dxf, "char_height", None) or 2.5)
            w = max(h * 0.8, len(text_val) * h * 0.6)  # Estimated bounding width

            ins = e.dxf.insert
            cx = float(ins[0]) + w * 0.5
            cy = float(ins[1]) + h * 0.5

            dxfattribs = {"layer": e.dxf.layer, "color": e.dxf.color}

            if self.shape == "CIRCLE":
                radius = 0.5 * math.hypot(w, h) * (1.0 + self.offset_factor)
                circle = space.add_circle((cx, cy), radius, dxfattribs=dxfattribs)
                self._created_enclosures.append(circle)
            elif self.shape == "RECTANGLE":
                pad_x = w * self.offset_factor * 0.5
                pad_y = h * self.offset_factor * 0.5
                pts = [
                    (cx - w/2 - pad_x, cy - h/2 - pad_y),
                    (cx + w/2 + pad_x, cy - h/2 - pad_y),
                    (cx + w/2 + pad_x, cy + h/2 + pad_y),
                    (cx - w/2 - pad_x, cy + h/2 + pad_y),
                ]
                poly = space.add_lwpolyline(pts, close=True, dxfattribs=dxfattribs)
                self._created_enclosures.append(poly)
            else:  # SLOT
                pad_x = w * self.offset_factor * 0.5
                pad_y = h * self.offset_factor * 0.5
                r = (h * 0.5) + pad_y
                pts = [
                    (cx - w/2, cy - r),
                    (cx + w/2, cy - r),
                    (cx + w/2, cy + r),
                    (cx - w/2, cy + r),
                ]
                poly = space.add_lwpolyline(pts, close=True, dxfattribs=dxfattribs)
                self._created_enclosures.append(poly)

        document.dirty = True

    def undo(self, document) -> None:
        space = self.space(document)
        for enc in self._created_enclosures:
            if enc.is_alive:
                space.unlink_entity(enc)
                document.doc.entitydb.delete_entity(enc)
        self._created_enclosures = []
        document.dirty = True


class TOrientCommand(Command):
    """TORIENT: Rotate text objects to a specific angle or readable orientation."""

    name = "TORIENT"

    def __init__(self, entities: Sequence, angle: Optional[float] = None) -> None:
        self.entities = [e for e in entities if getattr(e, "dxftype", lambda: "")() in ("TEXT", "MTEXT")]
        self.target_angle = angle
        self._snapshots: list[tuple[object, float]] = []

    def do(self, document) -> None:
        self._snapshots = []
        for e in self.entities:
            if not e.is_alive:
                continue
            old_rot = float(e.dxf.get("rotation", 0.0) or 0.0)
            self._snapshots.append((e, old_rot))

            if self.target_angle is not None:
                new_rot = self.target_angle % 360.0
            else:
                # Make readable: between 0 and 180 degrees
                norm = old_rot % 360.0
                if 90.0 < norm <= 270.0:
                    new_rot = (norm + 180.0) % 360.0
                else:
                    new_rot = norm

            e.dxf.rotation = new_rot
        document.dirty = True

    def undo(self, document) -> None:
        for e, old_rot in self._snapshots:
            if e.is_alive:
                e.dxf.rotation = old_rot
        self._snapshots = []
        document.dirty = True


# -----------------------------------------------------------------------------
# 3. MODIFY: FLATTEN (3D -> 2D Z=0), EXTRIM (Extended Boundary Trim)
# -----------------------------------------------------------------------------

class FlattenCommand(Command):
    """FLATTEN: Projects 3D geometry onto 2D by setting all Z coordinates to 0.0."""

    name = "FLATTEN"

    def __init__(self, entities: Sequence) -> None:
        self.entities = list(entities)
        self._snapshots: list[tuple[object, dict]] = []

    def do(self, document) -> None:
        self._snapshots = []
        for e in self.entities:
            if not e.is_alive:
                continue
            dxftype = e.dxftype()
            snap: dict = {}

            if dxftype == "LINE":
                snap["start"] = Vec3(e.dxf.start)
                snap["end"] = Vec3(e.dxf.end)
                e.dxf.start = (e.dxf.start.x, e.dxf.start.y, 0.0)
                e.dxf.end = (e.dxf.end.x, e.dxf.end.y, 0.0)
            elif dxftype == "POINT":
                snap["location"] = Vec3(e.dxf.location)
                e.dxf.location = (e.dxf.location.x, e.dxf.location.y, 0.0)
            elif dxftype in ("CIRCLE", "ARC", "ELLIPSE"):
                snap["center"] = Vec3(e.dxf.center)
                e.dxf.center = (e.dxf.center.x, e.dxf.center.y, 0.0)
            elif dxftype == "LWPOLYLINE":
                snap["elevation"] = float(e.dxf.get("elevation", 0.0) or 0.0)
                e.dxf.elevation = 0.0
            elif dxftype in ("TEXT", "MTEXT", "INSERT"):
                snap["insert"] = Vec3(e.dxf.insert)
                e.dxf.insert = (e.dxf.insert.x, e.dxf.insert.y, 0.0)
            elif dxftype == "POLYLINE":
                snap["vertices"] = [Vec3(v.dxf.location) for v in e.vertices]
                for v in e.vertices:
                    v.dxf.location = (v.dxf.location.x, v.dxf.location.y, 0.0)
            elif dxftype == "SPLINE":
                if hasattr(e, "control_points"):
                    snap["control_points"] = [Vec3(p) for p in e.control_points]
                    e.control_points = [(p[0], p[1], 0.0) for p in e.control_points]
                if hasattr(e, "fit_points"):
                    snap["fit_points"] = [Vec3(p) for p in e.fit_points]
                    e.fit_points = [(p[0], p[1], 0.0) for p in e.fit_points]

            if snap:
                self._snapshots.append((e, snap))

        document.dirty = True

    def undo(self, document) -> None:
        for e, snap in self._snapshots:
            if not e.is_alive:
                continue
            dxftype = e.dxftype()
            if dxftype == "LINE":
                e.dxf.start = snap["start"]
                e.dxf.end = snap["end"]
            elif dxftype == "POINT":
                e.dxf.location = snap["location"]
            elif dxftype in ("CIRCLE", "ARC", "ELLIPSE"):
                e.dxf.center = snap["center"]
            elif dxftype == "LWPOLYLINE":
                e.dxf.elevation = snap["elevation"]
            elif dxftype in ("TEXT", "MTEXT", "INSERT"):
                e.dxf.insert = snap["insert"]
            elif dxftype == "POLYLINE":
                for v, loc in zip(e.vertices, snap["vertices"]):
                    v.dxf.location = loc
            elif dxftype == "SPLINE":
                if "control_points" in snap:
                    e.control_points = snap["control_points"]
                if "fit_points" in snap:
                    e.fit_points = snap["fit_points"]

        self._snapshots = []
        document.dirty = True


class ExtrimCommand(Command):
    """EXTRIM: Extended boundary trim - trim objects crossing a polyline/circle boundary."""

    name = "EXTRIM"

    def __init__(self, boundary_entity, side_point: tuple[float, float]) -> None:
        self.boundary = boundary_entity
        self.side_point = (float(side_point[0]), float(side_point[1]))
        self._trimmed_snapshots: list[tuple[object, dict]] = []
        self._erased: list[object] = []

    def _is_point_inside(self, pt: tuple[float, float]) -> bool:
        """Ray-casting point in polygon test for the boundary."""
        pts: list[tuple[float, float]] = []
        if self.boundary.dxftype() == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in self.boundary.get_points()]
        elif self.boundary.dxftype() == "CIRCLE":
            c = self.boundary.dxf.center
            r = self.boundary.dxf.radius
            return math.hypot(pt[0] - c[0], pt[1] - c[1]) < r
        if len(pts) < 3:
            return False

        inside = False
        n = len(pts)
        x, y = pt
        p1x, p1y = pts[0]
        for i in range(n + 1):
            p2x, p2y = pts[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    def do(self, document) -> None:
        space = self.space(document)
        side_inside = self._is_point_inside(self.side_point)
        self._trimmed_snapshots = []
        self._erased = []

        # Process lines crossing or completely on the specified side
        for e in list(space):
            if e is self.boundary or not e.is_alive:
                continue

            if e.dxftype() == "LINE":
                p1 = (e.dxf.start.x, e.dxf.start.y)
                p2 = (e.dxf.end.x, e.dxf.end.y)
                in1 = self._is_point_inside(p1)
                in2 = self._is_point_inside(p2)

                # If both on target side: erase line
                if (in1 == side_inside) and (in2 == side_inside):
                    space.unlink_entity(e)
                    self._erased.append(e)
                elif in1 == side_inside or in2 == side_inside:
                    # Line crosses boundary: trim vertex on target side
                    snap = {"start": Vec3(e.dxf.start), "end": Vec3(e.dxf.end)}
                    self._trimmed_snapshots.append((e, snap))

                    # Simple mid-cut approximation
                    mid = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
                    if in1 == side_inside:
                        e.dxf.start = (mid[0], mid[1], e.dxf.start.z)
                    else:
                        e.dxf.end = (mid[0], mid[1], e.dxf.end.z)

        document.dirty = True

    def undo(self, document) -> None:
        space = self.space(document)
        for e, snap in self._trimmed_snapshots:
            if e.is_alive:
                e.dxf.start = snap["start"]
                e.dxf.end = snap["end"]
        for e in self._erased:
            space.add_entity(e)
        self._trimmed_snapshots = []
        self._erased = []
        document.dirty = True


# -----------------------------------------------------------------------------
# 4. DRAW: BREAKLINE (Zigzag breakline symbol)
# -----------------------------------------------------------------------------

class BreaklineCommand(Command):
    """BREAKLINE: Draws a standard drafting breakline with central zigzag."""

    name = "BREAKLINE"

    def __init__(self, p1: tuple[float, float], p2: tuple[float, float],
                 size: float = 5.0) -> None:
        self.p1 = (float(p1[0]), float(p1[1]))
        self.p2 = (float(p2[0]), float(p2[1]))
        self.size = size
        self._created: Optional[object] = None

    def do(self, document) -> None:
        space = self.space(document)
        dx = self.p2[0] - self.p1[0]
        dy = self.p2[1] - self.p1[1]
        dist = math.hypot(dx, dy)
        if dist < 1e-4:
            return

        ux, uy = dx / dist, dy / dist  # Unit vector along line
        nx, ny = -uy, ux               # Normal vector

        mid_dist = dist * 0.5
        w = max(1.0, min(dist * 0.2, self.size))
        h = w * 0.8

        p_pre = (self.p1[0] + ux * (mid_dist - w), self.p1[1] + uy * (mid_dist - w))
        p_up = (self.p1[0] + ux * (mid_dist - w * 0.3) + nx * h, self.p1[1] + uy * (mid_dist - w * 0.3) + ny * h)
        p_down = (self.p1[0] + ux * (mid_dist + w * 0.3) - nx * h, self.p1[1] + uy * (mid_dist + w * 0.3) - ny * h)
        p_post = (self.p1[0] + ux * (mid_dist + w), self.p1[1] + uy * (mid_dist + w))

        vertices = [self.p1, p_pre, p_up, p_down, p_post, self.p2]
        self._created = space.add_lwpolyline(vertices)
        document.dirty = True

    def undo(self, document) -> None:
        space = self.space(document)
        if self._created is not None and self._created.is_alive:
            space.unlink_entity(self._created)
            document.doc.entitydb.delete_entity(self._created)
            self._created = None
        document.dirty = True
