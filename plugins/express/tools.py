# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Interactive CAD tools for Express Tools."""
from __future__ import annotations

from typing import Optional
from core.i18n import tr
from tools.base import Tool, Point
from . import actions


class BurstTool(Tool):
    """BURST: Explode block references while preserving attribute values."""

    wants_selection = True

    def start(self) -> None:
        self.name = "BURST"
        self.prompt("Select block references with attributes to burst:")

    def on_selection(self, entities: list) -> None:
        inserts = [e for e in entities if getattr(e, "dxftype", lambda: "")() == "INSERT"]
        if not inserts:
            self.ctx.echo(tr("No block references found in selection."))
            self.ctx.finish()
            return

        cmd = actions.BurstCommand(inserts)
        self.ctx.execute(cmd)
        self.ctx.echo(tr("BURST: {count} block(s) exploded, attributes preserved as text.", count=len(inserts)))
        self.ctx.finish()


class BCountTool(Tool):
    """BCOUNT: Count block insertions and output a formatted summary."""

    wants_selection = True

    def start(self) -> None:
        self.name = "BCOUNT"
        self.prompt("Select blocks to count or <Enter for all>:")

    def on_selection(self, entities: list) -> None:
        doc = self.ctx.document
        if doc is None:
            self.ctx.finish()
            return

        summary = actions.bcount_summary(doc, entities if entities else None)
        if not summary:
            self.ctx.echo(tr("No block references found."))
            self.ctx.finish()
            return

        total = sum(c for _, c in summary)
        lines = [
            "",
            tr("=== BLOCK COUNT (Total: {total}) ===", total=total),
            f"{tr('Block Name'):<36} {tr('Count'):>8}",
            "-" * 46,
        ]
        for name, count in summary:
            lines.append(f"{name:<36} {count:>8}")
        lines.append("-" * 46)

        for line in lines:
            self.ctx.echo(line)
        self.ctx.finish()


class BlockReplaceTool(Tool):
    """BLOCKREPLACE: Replace all instances of one block with another."""

    def start(self) -> None:
        self.name = "BLOCKREPLACE"
        old = self.ctx.ask_text(tr("Enter block name to replace:"), "")
        if not old or not old.strip():
            self.ctx.finish()
            return
        new = self.ctx.ask_text(tr("Enter replacement block name:"), "")
        if not new or not new.strip():
            self.ctx.finish()
            return

        cmd = actions.BlockReplaceCommand(old.strip(), new.strip())
        self.ctx.execute(cmd)
        self.ctx.echo(tr("BLOCKREPLACE: Replaced '{old}' with '{new}'.", old=old.strip(), new=new.strip()))
        self.ctx.finish()


class Txt2MtxtTool(Tool):
    """TXT2MTXT: Combine multiple single-line text objects into one MTEXT."""

    wants_selection = True

    def start(self) -> None:
        self.name = "TXT2MTXT"
        self.prompt("Select TEXT objects to combine into MTEXT:")

    def on_selection(self, entities: list) -> None:
        texts = [e for e in entities if getattr(e, "dxftype", lambda: "")() == "TEXT"]
        if not texts:
            self.ctx.echo(tr("No TEXT objects in selection."))
            self.ctx.finish()
            return

        cmd = actions.Txt2MtxtCommand(texts)
        self.ctx.execute(cmd)
        self.ctx.echo(tr("TXT2MTXT: Combined {count} TEXT objects into 1 MTEXT.", count=len(texts)))
        self.ctx.finish()


class TCaseTool(Tool):
    """TCASE: Change text case (UPPER, lower, Title, Sentence)."""

    wants_selection = True

    def start(self) -> None:
        self.name = "TCASE"
        self._entities: list = []
        self.prompt("Select TEXT/MTEXT objects to change case:")

    def on_selection(self, entities: list) -> None:
        self._entities = [e for e in entities if getattr(e, "dxftype", lambda: "")() in ("TEXT", "MTEXT")]
        if not self._entities:
            self.ctx.echo(tr("No text objects selected."))
            self.ctx.finish()
            return
        self.prompt("Enter case mode [UPpercase/LOwercase/Title/Sentence] <UP>:")

    def on_text(self, text: str) -> None:
        raw = text.strip().upper()
        if raw.startswith("LO"):
            mode = "LOWER"
        elif raw.startswith("T"):
            mode = "TITLE"
        elif raw.startswith("S"):
            mode = "SENTENCE"
        else:
            mode = "UPPER"

        cmd = actions.TCaseCommand(self._entities, mode=mode)
        self.ctx.execute(cmd)
        self.ctx.echo(tr("TCASE: Converted {count} text object(s) to {mode}.", count=len(self._entities), mode=mode))
        self.ctx.finish()


class TCountTool(Tool):
    """TCOUNT: Automatically number text objects sequentially."""

    wants_selection = True

    def start(self) -> None:
        self.name = "TCOUNT"
        self._entities: list = []
        self._order = "Y"
        self._start = 1
        self._step = 1
        self._step_phase = 0
        self.prompt("Select text objects to number:")

    def on_selection(self, entities: list) -> None:
        self._entities = [e for e in entities if getattr(e, "dxftype", lambda: "")() in ("TEXT", "MTEXT")]
        if not self._entities:
            self.ctx.echo(tr("No text objects selected."))
            self.ctx.finish()
            return
        self._step_phase = 1
        self.prompt("Sort selected objects by [X-order/Y-order/Select-order] <Y>:")

    def on_text(self, text: str) -> None:
        t = text.strip().upper()
        if self._step_phase == 1:
            if t.startswith("X"):
                self._order = "X"
            elif t.startswith("S"):
                self._order = "SELECT"
            else:
                self._order = "Y"
            self._step_phase = 2
            self.prompt("Specify start, increment <1,1>:")
        elif self._step_phase == 2:
            if "," in t:
                parts = t.split(",")
                try:
                    self._start = int(parts[0].strip())
                    self._step = int(parts[1].strip())
                except ValueError:
                    pass
            elif t.isdigit():
                self._start = int(t)
            self._step_phase = 3
            self.prompt("Placement of numbers [Overwrite/Prefix/Suffix] <Overwrite>:")
        elif self._step_phase == 3:
            if t.startswith("P"):
                placement = "PREFIX"
            elif t.startswith("S"):
                placement = "SUFFIX"
            else:
                placement = "OVERWRITE"

            cmd = actions.TCountCommand(
                self._entities,
                start=self._start,
                step=self._step,
                order=self._order,
                placement=placement
            )
            self.ctx.execute(cmd)
            self.ctx.echo(tr("TCOUNT: Numbered {count} text object(s).", count=len(self._entities)))
            self.ctx.finish()


class TCircleTool(Tool):
    """TCIRCLE: Enclose text objects with Circle, Slot, or Rectangle."""

    wants_selection = True

    def start(self) -> None:
        self.name = "TCIRCLE"
        self._entities: list = []
        self.prompt("Select text objects to enclose:")

    def on_selection(self, entities: list) -> None:
        self._entities = [e for e in entities if getattr(e, "dxftype", lambda: "")() in ("TEXT", "MTEXT")]
        if not self._entities:
            self.ctx.echo(tr("No text objects selected."))
            self.ctx.finish()
            return
        self.prompt("Enclosing shape [Circles/Slots/Rectangles] <Circles>:")

    def on_text(self, text: str) -> None:
        t = text.strip().upper()
        if t.startswith("R"):
            shape = "RECTANGLE"
        elif t.startswith("S"):
            shape = "SLOT"
        else:
            shape = "CIRCLE"

        cmd = actions.TCircleCommand(self._entities, shape=shape)
        self.ctx.execute(cmd)
        self.ctx.echo(tr("TCIRCLE: Enclosed {count} text object(s) with {shape}.", count=len(self._entities), shape=shape))
        self.ctx.finish()


class TOrientTool(Tool):
    """TORIENT: Rotate text objects to specific angle or readable orientation."""

    wants_selection = True

    def start(self) -> None:
        self.name = "TORIENT"
        self._entities: list = []
        self.prompt("Select text objects to orient:")

    def on_selection(self, entities: list) -> None:
        self._entities = [e for e in entities if getattr(e, "dxftype", lambda: "")() in ("TEXT", "MTEXT")]
        if not self._entities:
            self.ctx.echo(tr("No text objects selected."))
            self.ctx.finish()
            return
        self.prompt("Specify new absolute rotation angle or [Readable] <Readable>:")

    def on_text(self, text: str) -> None:
        t = text.strip()
        angle = None
        try:
            angle = float(t)
        except ValueError:
            angle = None  # Automatic readable

        cmd = actions.TOrientCommand(self._entities, angle=angle)
        self.ctx.execute(cmd)
        self.ctx.echo(tr("TORIENT: Oriented {count} text object(s).", count=len(self._entities)))
        self.ctx.finish()


class FlattenTool(Tool):
    """FLATTEN: Project 3D geometry to 2D (Z=0)."""

    wants_selection = True

    def start(self) -> None:
        self.name = "FLATTEN"
        self._entities: list = []
        self.prompt("Select objects to flatten or <Enter for whole drawing>:")

    def on_selection(self, entities: list) -> None:
        doc = self.ctx.document
        if doc is None:
            self.ctx.finish()
            return

        target = entities if entities else list(doc.current_space())
        if not target:
            self.ctx.echo(tr("No objects found to flatten."))
            self.ctx.finish()
            return

        cmd = actions.FlattenCommand(target)
        self.ctx.execute(cmd)
        self.ctx.echo(tr("FLATTEN: Flattened {count} object(s) to Z=0.", count=len(target)))
        self.ctx.finish()


class ExtrimTool(Tool):
    """EXTRIM: Extended boundary trim."""

    def start(self) -> None:
        self.name = "EXTRIM"
        self._boundary = None
        self.prompt("Select polyline or circle cutting boundary:")

    def on_point(self, point: Point) -> None:
        if self._boundary is None:
            picked = getattr(self.ctx.services, "pick_entity", lambda p: None)(point)
            if picked is None or picked.dxftype() not in ("LWPOLYLINE", "CIRCLE"):
                self.prompt("Boundary must be a closed polyline or circle. Pick again:")
                return
            self._boundary = picked
            self.prompt("Specify the side to trim on (pick point inside or outside):")
        else:
            cmd = actions.ExtrimCommand(self._boundary, (point[0], point[1]))
            self.ctx.execute(cmd)
            self.ctx.echo(tr("EXTRIM: Objects trimmed at boundary."))
            self.ctx.finish()


class BreaklineTool(Tool):
    """BREAKLINE: Draw a drafting breakline with a zigzag."""

    def start(self) -> None:
        self.name = "BREAKLINE"
        self._p1 = None
        self.prompt("Specify first point for breakline:")

    def on_point(self, point: Point) -> None:
        if self._p1 is None:
            self._p1 = (point[0], point[1])
            self.last_point = point
            self.prompt("Specify second point for breakline:")
        else:
            p2 = (point[0], point[1])
            cmd = actions.BreaklineCommand(self._p1, p2)
            self.ctx.execute(cmd)
            self.ctx.finish()


TOOL_CLASSES = [
    ("BURST", BurstTool),
    ("BCOUNT", BCountTool),
    ("BLOCKREPLACE", BlockReplaceTool),
    ("TXT2MTXT", Txt2MtxtTool),
    ("TCASE", TCaseTool),
    ("TCOUNT", TCountTool),
    ("TCIRCLE", TCircleTool),
    ("TORIENT", TOrientTool),
    ("FLATTEN", FlattenTool),
    ("EXTRIM", ExtrimTool),
    ("BREAKLINE", BreaklineTool),
]
