# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""XATTACH: attach a drawing as an external reference (issue #33).

AutoCAD's XATTACH shows the Select Reference File dialog, then the Attach
External Reference dialog (reference type, path type, scale, insertion
point, rotation), and what was left "on screen" is asked at the prompt:
``Specify insertion point or [Scale/X/Y/Z/Rotate/PScale/PX/PY/PZ/PRotate]:``
(the INSERT prompt, p. 2106). Here the file dialog is the same, and the
dialog's two choices are two prompts with AutoCAD's own defaults --
Attachment and Relative path -- so Enter, Enter, click attaches a
colleague's plan exactly as most people do. Scale and rotation follow the
-XREF flow: X scale, Y scale (defaults to X), rotation.
"""
from __future__ import annotations

from pathlib import Path

from core import xrefs
from core.i18n import tr
from tools.base import Point, Tool


class XAttachTool(Tool):
    """XATTACH / -XREF Attach|Overlay."""

    def start(self) -> None:
        self.name = "XATTACH"
        self._path: Path | None = None
        self._overlay = bool(getattr(self.ctx.services, "_xref_overlay_next", False))
        try:
            self.ctx.services._xref_overlay_next = False
        except Exception:                 # noqa: BLE001 - a test double
            pass
        self._path_type = "Relative"
        self._insert: Point | None = None
        self._xscale = 1.0
        self._yscale: float | None = None
        self._rotation = 0.0
        self._await = "type"
        filename = self._pick_file()
        if not filename:
            self.ctx.finish()
            return
        self._path = Path(filename)
        self.prompt("Enter reference type [Attachment/Overlay] <{t}>:",
                    t=tr("Overlay") if self._overlay else tr("Attachment"))

    def _pick_file(self) -> str:
        pending = getattr(self.ctx.services, "_xref_file_next", None)
        if pending:
            try:
                self.ctx.services._xref_file_next = None
            except Exception:             # noqa: BLE001
                pass
            return str(pending)
        window = getattr(self.ctx.services, "window", None)
        if window is None:
            return ""
        from views import file_dialogs

        return file_dialogs.get_open_file(
            window, tr("Select Reference File"),
            tr("Drawings (*.dwg *.dxf);;All files (*)"))

    def wants_raw_text(self) -> bool:
        return self._await in ("type", "path", "xscale", "yscale", "rotation")

    # -- prompts -----------------------------------------------------------------
    def on_option(self, text: str) -> bool:
        t = self.option(text) or text.strip().upper()
        raw = text.strip()
        if self._await == "type":
            if t in ("", "A", "ATTACHMENT"):
                self._overlay = False if t else self._overlay
            elif t in ("O", "OVERLAY"):
                self._overlay = True
            else:
                return False
            self._await = "path"
            self.prompt("Enter path type [Full/Relative/No path] <Relative>:")
            return True
        if self._await == "path":
            if t in ("", "R", "RELATIVE"):
                self._path_type = "Relative"
            elif t in ("F", "FULL"):
                self._path_type = "Full"
            elif t in ("N", "NO", "NO PATH", "NOPATH"):
                self._path_type = "No path"
            else:
                return False
            self._await = "insert"
            self.prompt("Specify insertion point or [Scale/X/Y/Z/Rotate/"
                        "PScale/PX/PY/PZ/PRotate]:")
            return True
        if self._await == "insert":
            if t in ("S", "SCALE", "PS", "PSCALE", "X", "PX"):
                self._await = "xscale"
                self.prompt("Specify scale factor for XYZ axes <{v:g}>:", v=self._xscale)
                return True
            if t in ("Y", "PY"):
                self._await = "yscale"
                self.prompt("Specify Y scale factor <{v:g}>:", v=self._xscale)
                return True
            if t in ("Z", "PZ"):
                return True                 # 2D: the Z factor changes nothing
            if t in ("R", "ROTATE", "PR", "PROTATE"):
                self._await = "rotation"
                self.prompt("Specify rotation angle <{v:g}>:", v=self._rotation)
                return True
            return False
        if self._await in ("xscale", "yscale", "rotation"):
            if raw == "":
                value = None
            else:
                try:
                    value = float(raw)
                except ValueError:
                    return False
            if self._await == "xscale" and value is not None:
                self._xscale = value
            elif self._await == "yscale" and value is not None:
                self._yscale = value
            elif self._await == "rotation" and value is not None:
                self._rotation = value
            if self._insert is None:
                self._await = "insert"
                self.prompt("Specify insertion point or [Scale/X/Y/Z/Rotate/"
                            "PScale/PX/PY/PZ/PRotate]:")
            elif self._await == "xscale":
                self._await = "yscale"
                self.prompt("Enter Y scale factor <use X scale factor>:")
            elif self._await == "yscale":
                self._await = "rotation"
                self.prompt("Specify rotation angle <{v:g}>:", v=self._rotation)
            else:
                self._attach()
            return True
        return False

    def on_enter(self) -> None:
        if self._await in ("type", "path", "xscale", "yscale", "rotation"):
            self.on_option("")

    def on_point(self, point: Point) -> None:
        if self._await != "insert" or self._path is None:
            return
        self._insert = (point[0], point[1])
        # -XREF's flow after the point: X scale, Y scale, rotation
        self._await = "xscale"
        self.prompt("Enter X scale factor, specify opposite corner, or "
                    "[Corner/XYZ] <1>:")

    # -- the command -------------------------------------------------------------
    def _attach(self) -> None:
        document = getattr(self.ctx.services, "document", None)
        if document is None:
            window = getattr(self.ctx.services, "window", None)
            document = getattr(window, "document", None)
        doc = document.doc if document is not None else None
        host_path = getattr(document, "path", None)
        saved = xrefs.saved_path_for(host_path, self._path, self._path_type)
        stem = self._path.stem
        name = stem
        if doc is not None and name in doc.blocks and not xrefs.is_xref_block(
                doc.blocks.get(name)):
            name = xrefs.unique_name(doc, stem)     # a plain block took the name
        self.ctx.execute(xrefs.AttachXrefCommand(
            name, saved, self._insert, self._xscale, self._yscale,
            self._rotation + self.ucs().degrees,     # from the UCS's X (#91)
            overlay=self._overlay))
        self.ctx.echo(tr("Xref {name} attached ({kind}, {path}).",
                         name=name,
                         kind=tr("Overlay") if self._overlay else tr("Attach"),
                         path=saved))
        window = getattr(self.ctx.services, "window", None)
        changed = getattr(window, "xrefs_changed", None)
        if changed is not None:
            changed()
        self.ctx.finish()


def execute_bind(doc, xref_name: str, mode: str = "bind") -> bool:
    """Binds an external reference as a permanent local block.
    :param doc: Active ezdxf Drawing
    :param xref_name: Name of the XREF block
    :param mode: 'bind' (prefix Name$0$Layer) or 'insert' (merge layers)
    """
    import ezdxf

    if not doc or xref_name not in doc.blocks:
        return False

    block_def = doc.blocks[xref_name]
    raw_path = block_def.block.dxf.get("xref_path", None)
    if not raw_path:
        return False

    host_path = Path(doc.filename) if getattr(doc, "filename", None) else None
    resolved = xrefs.resolve_path(host_path, str(raw_path))
    if not resolved or not resolved.is_file():
        return False

    try:
        if resolved.suffix.lower() == ".dwg":
            from formats.dwg_bridge import load_dwg
            ext_doc = load_dwg(resolved).doc
        else:
            ext_doc = ezdxf.readfile(str(resolved))
    except Exception:
        return False

    # 1. Map and merge layers
    layer_map: dict[str, str] = {}
    for src_layer in ext_doc.layers:
        orig_name = src_layer.dxf.name
        if orig_name in ("0", "Defpoints"):
            target_name = orig_name
        else:
            target_name = f"{xref_name}$0${orig_name}" if mode == "bind" else orig_name

        layer_map[orig_name] = target_name

        if target_name not in doc.layers:
            color = src_layer.dxf.get("color", 7)
            linetype = src_layer.dxf.get("linetype", "Continuous")
            lineweight = src_layer.dxf.get("lineweight", -1)
            try:
                doc.layers.add(
                    name=target_name,
                    color=color,
                    linetype=linetype,
                    lineweight=lineweight,
                )
            except Exception:
                pass

    # 2. Transfer geometry from external modelspace to local block
    block_def.empty()
    for entity in ext_doc.modelspace():
        try:
            cloned = entity.copy()
            orig_l = cloned.dxf.get("layer", "0")
            cloned.dxf.layer = layer_map.get(orig_l, "0")
            block_def.add_entity(cloned)
        except Exception:
            continue

    # 3. Discard xref_path and clear XREF and OVERLAY flags
    block_def.block.dxf.discard("xref_path")
    flags = block_def.block.dxf.get("flags", 0)
    block_def.block.dxf.flags = flags & ~xrefs.BLK_XREF & ~xrefs.BLK_XREF_OVERLAY & ~32
    return True


class BindTool(Tool):
    """BIND: bind an external reference into the current drawing."""

    def start(self) -> None:
        self.name = "BIND"
        self._name = ""
        self._mode = "bind"
        self._await = "name"
        self.prompt("Enter name of xref to bind:")

    def wants_raw_text(self) -> bool:
        return True

    def on_option(self, text: str) -> bool:
        text = text.strip()
        if self._await == "name":
            if not text:
                self.ctx.finish()
                return True
            document = getattr(self.ctx.services, "document", None)
            doc = document.doc if document is not None else None
            if doc is None or text not in doc.blocks or not xrefs.is_xref_block(doc.blocks.get(text)):
                self.ctx.echo(tr("Xref {name} not found.", name=text))
                self.ctx.finish()
                return True
            self._name = text
            self._await = "mode"
            self.prompt("Enter Bind type [Bind/Insert] <Bind>:")
            return True
        if self._await == "mode":
            mode = "insert" if text.upper().startswith("I") else "bind"
            document = getattr(self.ctx.services, "document", None)
            doc = document.doc if document is not None else None
            if doc and execute_bind(doc, self._name, mode=mode):
                self.ctx.echo(tr("Xref {name} bound successfully.", name=self._name))
                window = getattr(self.ctx.services, "window", None)
                changed = getattr(window, "xrefs_changed", None)
                if changed is not None:
                    changed()
            else:
                self.ctx.echo(tr("Failed to bind xref {name}.", name=self._name))
            self.ctx.finish()
            return True
        return False

    def on_enter(self) -> None:
        if self._await == "mode":
            self.on_option("Bind")


XREF_TOOL_CLASSES = {"XATTACH": XAttachTool, "BIND": BindTool}
