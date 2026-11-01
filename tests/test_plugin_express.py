# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeCAD contributors.
"""Unit tests for the Express Tools plugin."""
from __future__ import annotations

import unittest
from ezdxf.math import Vec3

from core.commands import History
from core.document import Document
from plugins.express import actions
from plugins.express import PLUGIN


class TestExpressPlugin(unittest.TestCase):
    def setUp(self):
        self.doc = Document.new()
        self.msp = self.doc.modelspace()
        self.history = History(self.doc)

    def test_plugin_metadata(self):
        self.assertEqual(PLUGIN.id, "express")
        self.assertEqual(PLUGIN.name, "Express")
        self.assertIn("BURST", PLUGIN.tools)
        self.assertIn("FLATTEN", PLUGIN.tools)
        self.assertIn("TX", PLUGIN.aliases)
        self.assertEqual(PLUGIN.aliases["TX"], "TXT2MTXT")

    def test_burst_command(self):
        # 1. Define block with a line and two ATTDEF entities
        blk = self.doc.doc.blocks.new(name="BURST_BLK")
        blk.add_line((0, 0), (10, 0), dxfattribs={"layer": "0"})
        blk.add_attdef("TAG1", (2, 2), dxfattribs={"prompt": "P1", "text": "Default1"})
        blk.add_attdef("TAG2", (5, 2), dxfattribs={"prompt": "P2", "text": "Default2"})

        # 2. Insert block reference with ATTRIB entities
        insert = self.msp.add_blockref("BURST_BLK", (100, 100))
        insert.add_attrib("TAG1", "CustomValue1", (102, 102))
        insert.add_attrib("TAG2", "CustomValue2", (105, 102))

        # Ensure insert is in modelspace
        self.assertIn(insert, list(self.msp))

        # 3. Execute BurstCommand
        cmd = actions.BurstCommand([insert])
        self.history.execute(cmd)

        # Verify block reference is removed
        self.assertNotIn(insert, list(self.msp))

        # Verify exploded geometry exists in modelspace
        msp_entities = list(self.msp)
        lines = [e for e in msp_entities if e.dxftype() == "LINE"]
        texts = [e for e in msp_entities if e.dxftype() in ("TEXT", "MTEXT")]
        attdefs = [e for e in msp_entities if e.dxftype() == "ATTDEF"]

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].dxf.start, (100, 100, 0))
        self.assertEqual(lines[0].dxf.end, (110, 100, 0))

        # Verify ATTRIBs were converted to plain text with values
        text_contents = {t.dxf.text if hasattr(t.dxf, "text") else t.text for t in texts}
        self.assertIn("CustomValue1", text_contents)
        self.assertIn("CustomValue2", text_contents)
        # Verify ATTDEF entities were skipped (standard BURST behavior)
        self.assertEqual(len(attdefs), 0)

        # 4. Undo Burst
        self.history.undo()
        self.assertIn(insert, list(self.msp))
        self.assertEqual(len([e for e in self.msp if e.dxftype() == "LINE"]), 0)

        # 5. Redo Burst
        self.history.redo()
        self.assertNotIn(insert, list(self.msp))
        self.assertEqual(len([e for e in self.msp if e.dxftype() == "LINE"]), 1)

    def test_bcount_summary(self):
        self.doc.doc.blocks.new(name="PUMP")
        self.doc.doc.blocks.new(name="VALVE")

        self.msp.add_blockref("PUMP", (0, 0))
        self.msp.add_blockref("PUMP", (10, 0))
        self.msp.add_blockref("VALVE", (20, 0))

        summary = dict(actions.bcount_summary(self.doc))
        self.assertIn("PUMP", summary)
        self.assertIn("VALVE", summary)
        self.assertEqual(summary["PUMP"], 2)
        self.assertEqual(summary["VALVE"], 1)

    def test_blockreplace_command(self):
        self.doc.doc.blocks.new(name="OLD_BLOCK")
        self.doc.doc.blocks.new(name="NEW_BLOCK")

        ins1 = self.msp.add_blockref("OLD_BLOCK", (0, 0))
        ins2 = self.msp.add_blockref("OLD_BLOCK", (10, 10))

        cmd = actions.BlockReplaceCommand("OLD_BLOCK", "NEW_BLOCK")
        self.history.execute(cmd)

        self.assertEqual(ins1.dxf.name, "NEW_BLOCK")
        self.assertEqual(ins2.dxf.name, "NEW_BLOCK")

        self.history.undo()
        self.assertEqual(ins1.dxf.name, "OLD_BLOCK")
        self.assertEqual(ins2.dxf.name, "OLD_BLOCK")

        self.history.redo()
        self.assertEqual(ins1.dxf.name, "NEW_BLOCK")
        self.assertEqual(ins2.dxf.name, "NEW_BLOCK")

    def test_txt2mtxt_command(self):
        t1 = self.msp.add_text("First line", dxfattribs={"insert": (0, 10)})
        t2 = self.msp.add_text("Second line", dxfattribs={"insert": (0, 0)})

        cmd = actions.Txt2MtxtCommand([t1, t2])
        self.history.execute(cmd)

        self.assertNotIn(t1, list(self.msp))
        self.assertNotIn(t2, list(self.msp))

        mtexts = [e for e in self.msp if e.dxftype() == "MTEXT"]
        self.assertEqual(len(mtexts), 1)
        self.assertIn("First line", mtexts[0].text)
        self.assertIn("Second line", mtexts[0].text)

        self.history.undo()
        self.assertIn(t1, list(self.msp))
        self.assertIn(t2, list(self.msp))
        self.assertEqual(len([e for e in self.msp if e.dxftype() == "MTEXT"]), 0)

    def test_tcase_command(self):
        t = self.msp.add_text("hello world", dxfattribs={"insert": (0, 0)})

        cmd_upper = actions.TCaseCommand([t], mode="UPPER")
        self.history.execute(cmd_upper)
        self.assertEqual(t.dxf.text, "HELLO WORLD")

        self.history.undo()
        self.assertEqual(t.dxf.text, "hello world")

        cmd_title = actions.TCaseCommand([t], mode="Title")
        self.history.execute(cmd_title)
        self.assertEqual(t.dxf.text, "Hello World")

    def test_tcount_command(self):
        t1 = self.msp.add_text("ITEM", dxfattribs={"insert": (10, 0)})
        t2 = self.msp.add_text("ITEM", dxfattribs={"insert": (0, 0)})
        t3 = self.msp.add_text("ITEM", dxfattribs={"insert": (20, 0)})

        # Sort by X: t2 (0,0) -> 1, t1 (10,0) -> 2, t3 (20,0) -> 3
        cmd = actions.TCountCommand([t1, t2, t3], start=1, step=1, sort_mode="X", placement="Prefix")
        self.history.execute(cmd)

        self.assertEqual(t2.dxf.text, "1 ITEM")
        self.assertEqual(t1.dxf.text, "2 ITEM")
        self.assertEqual(t3.dxf.text, "3 ITEM")

        self.history.undo()
        self.assertEqual(t1.dxf.text, "ITEM")
        self.assertEqual(t2.dxf.text, "ITEM")
        self.assertEqual(t3.dxf.text, "ITEM")

    def test_tcircle_command(self):
        t = self.msp.add_text("NOTE", dxfattribs={"insert": (10, 10)})

        # Enclose with Circle
        cmd = actions.TCircleCommand([t], shape="Circle", offset_factor=0.35)
        self.history.execute(cmd)

        circles = [e for e in self.msp if e.dxftype() == "CIRCLE"]
        self.assertEqual(len(circles), 1)

        self.history.undo()
        self.assertEqual(len([e for e in self.msp if e.dxftype() == "CIRCLE"]), 0)

        # Enclose with Box
        cmd_box = actions.TCircleCommand([t], shape="Box", offset_factor=0.35)
        self.history.execute(cmd_box)
        polys = [e for e in self.msp if e.dxftype() == "LWPOLYLINE"]
        self.assertEqual(len(polys), 1)

    def test_torient_command(self):
        t = self.msp.add_text("UPSIDE DOWN", dxfattribs={"insert": (0, 0), "rotation": 270})
        cmd = actions.TOrientCommand([t], angle=None)  # Auto readable [0, 180)
        self.history.execute(cmd)

        self.assertEqual(t.dxf.rotation, 90.0)

        self.history.undo()
        self.assertEqual(t.dxf.rotation, 270.0)

    def test_flatten_command(self):
        l = self.msp.add_line((1, 2, 50), (10, 20, 100))
        p = self.msp.add_point((5, 5, 25))
        c = self.msp.add_circle((0, 0, 10), radius=5)

        cmd = actions.FlattenCommand([l, p, c])
        self.history.execute(cmd)

        self.assertEqual(l.dxf.start, (1, 2, 0))
        self.assertEqual(l.dxf.end, (10, 20, 0))
        self.assertEqual(p.dxf.location, (5, 5, 0))
        self.assertEqual(c.dxf.center, (0, 0, 0))

        self.history.undo()
        self.assertEqual(l.dxf.start, (1, 2, 50))
        self.assertEqual(l.dxf.end, (10, 20, 100))
        self.assertEqual(p.dxf.location, (5, 5, 25))
        self.assertEqual(c.dxf.center, (0, 0, 10))

    def test_breakline_command(self):
        cmd = actions.BreaklineCommand((0, 0), (100, 0))
        self.history.execute(cmd)

        polys = [e for e in self.msp if e.dxftype() == "LWPOLYLINE"]
        self.assertEqual(len(polys), 1)
        pts = list(polys[0].get_points("xy"))
        self.assertEqual(len(pts), 6)  # Start, pA, pPeak1, pPeak2, pB, End

        self.history.undo()
        self.assertEqual(len([e for e in self.msp if e.dxftype() == "LWPOLYLINE"]), 0)

    def test_extrim_command(self):
        circle = self.msp.add_circle((0, 0, 0), radius=10.0)
        # Line completely outside
        l_out = self.msp.add_line((20, 20), (30, 30))
        # Line crossing circle boundary
        l_cross = self.msp.add_line((0, 0), (20, 0))

        # Trim outside (pick point at 15, 0 which is outside circle r=10)
        cmd = actions.ExtrimCommand(circle, side_point=(15, 0))
        self.history.execute(cmd)

        # l_out was completely on the outside side -> erased
        self.assertNotIn(l_out, list(self.msp))
        # l_cross had vertex on outside side -> trimmed
        self.assertIn(l_cross, list(self.msp))
        self.assertNotEqual(l_cross.dxf.end, (20, 0, 0))

        # Undo restores l_out and original coordinates
        self.history.undo()
        self.assertIn(l_out, list(self.msp))
        self.assertEqual(l_cross.dxf.end, (20, 0, 0))


if __name__ == "__main__":
    unittest.main()
