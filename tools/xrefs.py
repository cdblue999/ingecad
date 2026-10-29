"""
IngeCAD — External References (XREFs) Management & Tools.
Provides XATTACH, XREF palette, Reload, Unload, Detach, and BIND operations.
"""

import os
import shutil
import subprocess
import tempfile
from typing import Optional, List, Dict, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QFileDialog,
    QMessageBox,
    QMenu,
    QLabel,
    QHeaderView,
    QInputDialog,
)

import ezdxf
from ezdxf.document import Drawing


def resolve_xref_path(drawing_path: Optional[str], raw_path: str) -> str:
    """Rozwiązuje ścieżkę względną lub bezwzględną pliku odnośnika."""
    if os.path.isabs(raw_path):
        return os.path.normpath(raw_path)
    base_dir = os.path.dirname(drawing_path) if drawing_path else os.getcwd()
    return os.path.normpath(os.path.join(base_dir, raw_path))


def ensure_dxf_format(file_path: str) -> Tuple[Optional[str], bool]:
    """
    Weryfikuje format pliku. Jeśli plik to DWG, dokonuje tymczasowej konwersji
    do DXF za pomocą systemowego lub zintegrowanego konwertera dwg2dxf.
    Zwraca (ścieżka_do_dxf, czy_tymczasowy).
    """
    if not os.path.exists(file_path):
        return None, False

    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".dxf":
        return file_path, False

    if ext == ".dwg":
        conv_bin = shutil.which("dwg2dxf")
        if not conv_bin:
            # Sprawdzenie ścieżki vendor w strukturze IngeCAD
            vendor_bin = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "vendor", "bin", "dwg2dxf")
            )
            if os.path.exists(vendor_bin):
                conv_bin = vendor_bin
            elif os.path.exists(vendor_bin + ".exe"):
                conv_bin = vendor_bin + ".exe"

        if conv_bin:
            tmp_dxf = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
            tmp_dxf.close()
            try:
                res = subprocess.run(
                    [conv_bin, "-y", "-o", tmp_dxf.name, file_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                )
                if res.returncode == 0 and os.path.exists(tmp_dxf.name):
                    return tmp_dxf.name, True
            except Exception as e:
                print(f"[XREF] Błąd konwersji DWG do DXF: {e}")
                if os.path.exists(tmp_dxf.name):
                    os.unlink(tmp_dxf.name)
        else:
            print("[XREF] Brak konwertera dwg2dxf w PATH ani w folderze vendor.")

    return None, False


def execute_bind(doc: Drawing, xref_name: str, mode: str = "bind") -> bool:
    """
    Wciela zewnętrzny odnośnik XREF jako stały blok w bieżącym dokumencie (AutoCAD BIND/INSERT).
    :param doc: Aktywny dokument ezdxf Drawing
    :param xref_name: Nazwa bloku XREF
    :param mode: 'bind' (prefiks Nazwa$0$Warstwa) lub 'insert' (scalenie warstw)
    """
    if not doc or xref_name not in doc.blocks:
        return False

    block_def = doc.blocks[xref_name]
    raw_path = block_def.block.dxf.get("xref_path", None)
    if not raw_path:
        return False

    full_path = resolve_xref_path(doc.filename, raw_path)
    if not os.path.exists(full_path):
        print(f"[BIND] Błąd: Plik źródłowy {full_path} nie istnieje.")
        return False

    dxf_path, is_temp = ensure_dxf_format(full_path)
    if not dxf_path:
        print(f"[BIND] Nie można odczytać geometrii z {full_path}")
        return False

    try:
        ext_doc = ezdxf.readfile(dxf_path)
    except Exception as err:
        print(f"[BIND] Błąd parsowania pliku DXF {dxf_path}: {err}")
        if is_temp and os.path.exists(dxf_path):
            os.unlink(dxf_path)
        return False

    # 1. Mapowanie i scalanie tabeli warstw
    layer_map: Dict[str, str] = {}
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

    # 2. Przeniesienie geometrii z modelu zewnętrznego do lokalnego bloku
    block_def.empty()

    for entity in ext_doc.modelspace():
        try:
            cloned = entity.copy()
            orig_l = cloned.dxf.get("layer", "0")
            cloned.dxf.layer = layer_map.get(orig_l, "0")
            block_def.add_entity(cloned)
        except Exception as copy_err:
            continue

    # 3. Zmiana typu bloku: usunięcie atrybutu xref_path i zresetowanie flag DXF
    block_def.block.dxf.discard("xref_path")
    flags = block_def.block.dxf.get("flags", 0)
    # Wyzerowanie flagi bitowej 4 (XREF) oraz 32 (Resolved XREF)
    block_def.block.dxf.flags = flags & ~4 & ~32

    if is_temp and os.path.exists(dxf_path):
        os.unlink(dxf_path)

    print(f"[BIND] Sukces: Pomyślnie związano XREF '{xref_name}' w trybie '{mode}'.")
    return True


class XrefManagerDialog(QDialog):
    """Okno dialogowe zarządzania zewnętrznymi odnośnikami (XREF Manager)."""

    xref_modified = Signal()

    def __init__(self, doc: Drawing, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("External References (XREF)")
        self.resize(650, 380)

        layout = QVBoxLayout(self)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Reference Name", "Status", "Size", "Type", "Saved Path"])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.tree)

        btn_layout = QHBoxLayout()
        self.btn_attach = QPushButton("Attach DWG/DXF...", self)
        self.btn_attach.clicked.connect(self._on_attach)
        self.btn_reload = QPushButton("Reload", self)
        self.btn_reload.clicked.connect(self._on_reload)
        self.btn_detach = QPushButton("Detach", self)
        self.btn_detach.clicked.connect(self._on_detach)
        self.btn_bind = QPushButton("Bind...", self)
        self.btn_bind.clicked.connect(self._on_bind_clicked)
        self.btn_close = QPushButton("Close", self)
        self.btn_close.clicked.connect(self.accept)

        btn_layout.addWidget(self.btn_attach)
        btn_layout.addWidget(self.btn_reload)
        btn_layout.addWidget(self.btn_detach)
        btn_layout.addWidget(self.btn_bind)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

        self.refresh_list()

    def refresh_list(self):
        self.tree.clear()
        if not self.doc:
            return

        for block in self.doc.blocks:
            xref_path = block.block.dxf.get("xref_path", None)
            if xref_path is not None:
                full_path = resolve_xref_path(self.doc.filename, xref_path)
                exists = os.path.exists(full_path)
                status = "Loaded" if exists else "Not Found"
                size_str = (
                    f"{os.path.getsize(full_path) // 1024} KB"
                    if exists
                    else "—"
                )
                ext = os.path.splitext(full_path)[1].upper().replace(".", "") or "DXF"

                item = QTreeWidgetItem([
                    block.name,
                    status,
                    size_str,
                    ext,
                    xref_path,
                ])
                if not exists:
                    item.setForeground(1, Qt.GlobalColor.red)
                self.tree.addTopLevelItem(item)

    def _get_selected_name(self) -> Optional[str]:
        item = self.tree.currentItem()
        return item.text(0) if item else None

    def _on_context_menu(self, pos):
        name = self._get_selected_name()
        if not name:
            return
        menu = QMenu(self)
        act_reload = menu.addAction("Reload")
        act_detach = menu.addAction("Detach")
        menu.addSeparator()
        bind_menu = menu.addMenu("Bind")
        act_bind_std = bind_menu.addAction("Bind (Bind type)")
        act_bind_ins = bind_menu.addAction("Insert (Merge layers)")

        act_reload.triggered.connect(self._on_reload)
        act_detach.triggered.connect(self._on_detach)
        act_bind_std.triggered.connect(lambda: self._do_bind(name, "bind"))
        act_bind_ins.triggered.connect(lambda: self._do_bind(name, "insert"))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_bind_clicked(self):
        name = self._get_selected_name()
        if not name:
            QMessageBox.information(self, "XREF", "Wybierz podkład XREF z listy.")
            return

        items = ["Bind (dodaj prefiks do warstw)", "Insert (scal warstwy z rysunkiem)"]
        item, ok = QInputDialog.getItem(
            self, "Bind Xref", f"Wybierz metodę związania dla: {name}", items, 0, False
        )
        if ok and item:
            mode = "bind" if "Bind" in item else "insert"
            self._do_bind(name, mode)

    def _do_bind(self, name: str, mode: str):
        confirm = QMessageBox.question(
            self,
            "Potwierdzenie BIND",
            f"Czy na pewno chcesz wtopić odnośnik '{name}' do rysunku jako blok lokalny?\n"
            f"Tryb: {mode.upper()}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            success = execute_bind(self.doc, name, mode=mode)
            if success:
                QMessageBox.information(
                    self, "Sukces", f"Odnośnik '{name}' został pomyślnie związany jako blok lokalny."
                )
                self.refresh_list()
                self.xref_modified.emit()
            else:
                QMessageBox.critical(
                    self, "Błąd", f"Nie udało się wykonać operacji BIND dla '{name}'."
                )

    def _on_reload(self):
        name = self._get_selected_name()
        if not name:
            return
        self.refresh_list()
        self.xref_modified.emit()

    def _on_detach(self):
        name = self._get_selected_name()
        if not name or not self.doc:
            return

        confirm = QMessageBox.question(
            self,
            "Detach Xref",
            f"Czy na pewno odłączyć referencję '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            # Usunięcie instancji INSERT z modelspace
            msp = self.doc.modelspace()
            to_remove = [e for e in msp if e.dxftype() == "INSERT" and e.dxf.name == name]
            for ins in to_remove:
                msp.delete_entity(ins)

            # Usunięcie definicji bloku
            if name in self.doc.blocks:
                self.doc.blocks.delete_block(name, safe=False)

            self.refresh_list()
            self.xref_modified.emit()

    def _on_attach(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Dołącz odnośnik zewnętrzny (XATTACH)", "", "CAD Drawings (*.dwg *.dxf);;All Files (*)"
        )
        if not file_path:
            return

        base_name = os.path.splitext(os.path.basename(file_path))[0]
        ref_name = base_name
        idx = 1
        while ref_name in self.doc.blocks:
            ref_name = f"{base_name}_{idx}"
            idx += 1

        rel_path = file_path
        if self.doc.filename:
            try:
                rel_path = os.path.relpath(file_path, os.path.dirname(self.doc.filename))
            except ValueError:
                rel_path = file_path

        # Utworzenie bloku XREF
        block = self.doc.blocks.new(name=ref_name)
        block.block.dxf.flags = block.block.dxf.get("flags", 0) | 4
        block.block.dxf.xref_path = rel_path

        # Wstawienie INSERT w punkcie (0, 0, 0)
        self.doc.modelspace().add_blockref(ref_name, (0, 0, 0))

        self.refresh_list()
        self.xref_modified.emit()


def run_bind_command(doc: Drawing, args: List[str]) -> bool:
    """
    Punkt wejściowy dla wiersza poleceń CLI (komenda BIND lub -XREF B).
    Składnia: BIND <nazwa_xref> [BIND/INSERT]
    """
    if not args:
        print("Użycie: BIND <nazwa_odnośnika> [bind|insert]")
        return False

    xref_name = args[0]
    mode = "bind"
    if len(args) > 1 and args[1].lower() in ("insert", "i"):
        mode = "insert"

    return execute_bind(doc, xref_name, mode=mode)
