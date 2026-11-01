# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeCAD contributors.
"""Express Tools -- productivity extensions for drafting, text, blocks and modify.

Inspired by AutoCAD Express Tools:
- Blocks: BURST (explode preserving attribute values), BCOUNT (block count summary),
  BLOCKREPLACE (replace block references across document).
- Text: TXT2MTXT (merge text entities into mtext), TCASE (text case conversion),
  TCOUNT (sequential text numbering), TCIRCLE (enclose text in circle/box/slot),
  TORIENT (orient text readability or angle).
- Modify: FLATTEN (flatten 3D geometry to 2D elevation Z=0), EXTRIM (boundary trim).
- Draw: BREAKLINE (draw engineering breakline with central zigzag).
"""
from __future__ import annotations

from pathlib import Path

from core.plugins import SEPARATOR, MenuItem, PluginSpec, Submenu, ToolbarItem

from .tools import TOOL_CLASSES

PLUGIN = PluginSpec(
    id="express",
    name="Express",
    version="1.0.0",
    description="Productivity tools for text, blocks, drafting and geometry in the spirit of AutoCAD Express Tools.",
    tools=dict(TOOL_CLASSES),
    aliases={
        "TX": "TXT2MTXT",
        "FL": "FLATTEN",
        "BRK": "BREAKLINE",
        "BC": "BCOUNT",
        "BRS": "BURST",
        "TC": "TCIRCLE",
        "TOR": "TORIENT",
        "BREP": "BLOCKREPLACE",
    },
    menu=(
        Submenu("Blocks", (
            MenuItem("Burst attributes to text (BURST)", "BURST"),
            MenuItem("Count block references (BCOUNT)", "BCOUNT"),
            SEPARATOR,
            MenuItem("Replace block definitions (BLOCKREPLACE)...", "BLOCKREPLACE"),
        )),
        Submenu("Text", (
            MenuItem("Convert Text to MText (TXT2MTXT)", "TXT2MTXT"),
            MenuItem("Change text case (TCASE)...", "TCASE"),
            MenuItem("Auto-number text (TCOUNT)...", "TCOUNT"),
            MenuItem("Enclose text with shape (TCIRCLE)...", "TCIRCLE"),
            MenuItem("Orient text angle (TORIENT)...", "TORIENT"),
        )),
        Submenu("Modify", (
            MenuItem("Flatten 3D to 2D (FLATTEN)", "FLATTEN"),
            MenuItem("Extended boundary trim (EXTRIM)...", "EXTRIM"),
        )),
        Submenu("Draw", (
            MenuItem("Breakline symbol (BREAKLINE)...", "BREAKLINE"),
        )),
    ),
    toolbar=(
        ToolbarItem("Burst attributes to text", "BURST"),
        ToolbarItem("Count blocks", "BCOUNT"),
        ToolbarItem("Convert Text to MText", "TXT2MTXT"),
        ToolbarItem("Change text case", "TCASE"),
        ToolbarItem("Auto-number text", "TCOUNT"),
        ToolbarItem("Enclose text", "TCIRCLE"),
        ToolbarItem("Flatten to 2D", "FLATTEN"),
        ToolbarItem("Extended trim", "EXTRIM"),
        ToolbarItem("Breakline", "BREAKLINE"),
    ),
    i18n_dir=Path(__file__).parent / "i18n",
)
