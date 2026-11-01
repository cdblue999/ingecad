# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""External References palette (XREF / EXTERNALREFERENCES, issue #33).

AutoCAD's palette lists every reference with its status, type and path
-- drawings, raster images and PDFs alike (Rafael, review 6: ours listed
only the DWGs) -- and offers Attach (DWG, Image, PDF), Detach, Reload
and Unload. Here it is a tab of the
right sidebar (managers live there, not in modal dialogs), refreshed when
a drawing opens or an xref changes. Every button routes through the same
Commands and helpers the -XREF command line uses (core.xrefs), so undo
and the picture agree whichever way the user went.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QHeaderView, QMenu, QPushButton, QRadioButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from core import xrefs
from core.i18n import tr

COLUMNS = ("Reference Name", "Status", "Type", "Saved Path")


class BindXrefDialog(QDialog):
    """AutoCAD-standard Bind Xrefs dialog.

    Allows choosing between Bind (namespaced with $0$) and Insert (direct symbol merge).
    """

    def __init__(self, parent=None, xref_name: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Bind Xrefs"))
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)

        group = QGroupBox(tr("Bind Type"), self)
        group_layout = QVBoxLayout(group)

        self.bind_radio = QRadioButton(tr("&Bind"), group)
        self.bind_radio.setChecked(True)
        self.bind_radio.setToolTip(
            tr("Prefixes dependent symbol names (layers, styles) with {name}$0$...", name=xref_name)
        )
        group_layout.addWidget(self.bind_radio)

        self.insert_radio = QRadioButton(tr("&Insert"), group)
        self.insert_radio.setToolTip(
            tr("Merges dependent symbols directly into the drawing without prefix.")
        )
        group_layout.addWidget(self.insert_radio)

        layout.addWidget(group)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    @property
    def mode(self) -> str:
        return "insert" if self.insert_radio.isChecked() else "bind"


class XrefsPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels([tr(c) for c in COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.attach_btn = QPushButton(tr("Attach..."), self)
        self.bind_btn = QPushButton(tr("Bind..."), self)
        self.detach_btn = QPushButton(tr("Detach"), self)
        self.reload_btn = QPushButton(tr("Reload"), self)
        self.unload_btn = QPushButton(tr("Unload"), self)
        for button in (self.attach_btn, self.bind_btn, self.detach_btn,
                       self.reload_btn, self.unload_btn):
            button.setFocusPolicy(Qt.NoFocus)
            row.addWidget(button)
        layout.addLayout(row)
        # AutoCAD's Attach button opens a choice of what to attach
        self._attach_menu = QMenu(self)
        for label, command in ((tr("Attach DWG..."), "XATTACH"),
                               (tr("Attach Image..."), "IMAGEATTACH"),
                               (tr("Attach PDF..."), "PDFATTACH")):
            action = self._attach_menu.addAction(label)
            action.triggered.connect(
                lambda _=False, c=command: self.window.tools.start_tool(c))
        self.attach_btn.setMenu(self._attach_menu)
        self.bind_btn.clicked.connect(self._bind)
        self.detach_btn.clicked.connect(self._detach)
        self.reload_btn.clicked.connect(self._reload)
        self.unload_btn.clicked.connect(self._unload)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self._sync_buttons()

    # -- contents ------------------------------------------------------------------
    def refresh(self) -> None:
        document = getattr(self.window, "document", None)
        self.table.setRowCount(0)
        if document is None:
            self._sync_buttons()
            return
        kinds = {"image": tr("Raster image"), "pdf": tr("PDF (as image)")}
        for ref in xrefs.references(document) + xrefs.image_references(document):
            r = self.table.rowCount()
            self.table.insertRow(r)
            kind = kinds.get(ref.kind) or (tr("Overlay") if ref.overlay else tr("Attach"))
            cells = (ref.name, tr(ref.status), kind, ref.path)
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setData(Qt.UserRole, (ref.kind, ref.handle or ref.name))
                if ref.status == "Not found":
                    item.setForeground(Qt.red)
                elif ref.status == "Unloaded":
                    item.setForeground(Qt.gray)
                self.table.setItem(r, c, item)
        self._sync_buttons()

    def selected(self) -> tuple[str, str] | None:
        """(kind, key) of the selected row: ("dwg", block name) or
        ("image"/"pdf", IMAGEDEF handle)."""
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item is not None else None

    def selected_name(self) -> str | None:
        """The selected DRAWING reference's name (the -XREF actions)."""
        chosen = self.selected()
        return chosen[1] if chosen is not None and chosen[0] == "dwg" else None

    def _sync_buttons(self) -> None:
        chosen = self.selected()
        is_dwg = chosen is not None and chosen[0] == "dwg"
        self.bind_btn.setEnabled(is_dwg)
        self.detach_btn.setEnabled(chosen is not None)
        self.reload_btn.setEnabled(chosen is not None)
        self.unload_btn.setEnabled(is_dwg)
        self.attach_btn.setEnabled(getattr(self.window, "document", None) is not None)

    def _context_menu(self, pos) -> None:
        chosen = self.selected()
        if chosen is None:
            return
        menu = QMenu(self)
        if chosen[0] == "dwg":
            act_open = menu.addAction(tr("Open"))
            act_open.triggered.connect(self._open_selected)
            menu.addSeparator()
            act_bind = menu.addAction(tr("Bind..."))
            act_bind.triggered.connect(self._bind)
            act_unload = menu.addAction(tr("Unload"))
            act_unload.triggered.connect(self._unload)
        act_reload = menu.addAction(tr("Reload"))
        act_reload.triggered.connect(self._reload)
        act_detach = menu.addAction(tr("Detach"))
        act_detach.triggered.connect(self._detach)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _open_selected(self) -> None:
        name = self.selected_name()
        if name is None:
            return
        document = getattr(self.window, "document", None)
        if document is None:
            return
        block = document.doc.blocks.get(name)
        if block is None:
            return
        raw_path = xrefs.xref_path_of(block)
        resolved = xrefs.resolve_path(getattr(document, "path", None), raw_path)
        if resolved is not None and resolved.is_file():
            self.window.open_paths([resolved])
        else:
            self.window.command_line.echo(tr("Could not find file: {path}", path=raw_path))

    def _bind(self) -> None:
        name = self.selected_name()
        if name is None:
            return
        dlg = BindXrefDialog(self, xref_name=name)
        if dlg.exec() != QDialog.Accepted:
            return
        mode = dlg.mode
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication

        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        self.window._loading_begin(tr("Binding external reference {name}...", name=name))
        QApplication.processEvents()
        try:
            self.window.tools._execute(xrefs.BindXrefCommand(name, mode=mode))
            self.window.command_line.echo(
                tr("Xref {name} bound ({mode}).", name=name, mode=mode.capitalize())
            )
            self.window.xrefs_changed()
        except Exception as exc:
            self.window.command_line.echo(
                tr("Failed to bind xref {name}: {error}", name=name, error=exc)
            )
        finally:
            self.window._loading_end()
            QGuiApplication.restoreOverrideCursor()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        # images are attached by tools that do not report here: the tab
        # reads the drawing whenever it comes into view
        self.refresh()
        super().showEvent(event)

    # -- actions ---------------------------------------------------------------------
    def _detach(self) -> None:
        chosen = self.selected()
        if chosen is not None and chosen[0] != "dwg":
            self.window.tools._execute(xrefs.DetachImageCommand(chosen[1]))
            self.window.command_line.echo(tr("Image detached."))
            self.window.xrefs_changed()
            return
        name = self.selected_name()
        if name is None:
            return
        self.window.tools._execute(xrefs.DetachXrefCommand(name))
        self.window.command_line.echo(tr("Xref {name} detached.", name=name))
        self.window.xrefs_changed()

    def _reload(self) -> None:
        chosen = self.selected()
        if chosen is not None and chosen[0] != "dwg":
            # an image is read from its file by every regen: one regen reloads it
            self.window.command_line.echo(tr("Image reloaded."))
            self.window.xrefs_changed()
            return
        name = self.selected_name()
        if name is None:
            return
        xrefs.reload(self.window.document, [name])
        self.window.command_line.echo(tr("Xref {name} reloaded.", name=name))
        self.window.xrefs_changed()

    def _unload(self) -> None:
        name = self.selected_name()
        if name is None:
            return
        xrefs.unload(self.window.document, [name])
        self.window.command_line.echo(tr("Xref {name} unloaded.", name=name))
        self.window.xrefs_changed()
