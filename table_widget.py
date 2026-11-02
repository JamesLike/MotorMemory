"""
One tab of the motor presets GUI: a table whose columns are motor PVs and
whose rows are named preset positions.  Row 0 is a pinned, read-only LIVE
row showing the current readback of every motor.
"""

from __future__ import annotations

import typing

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QKeySequence
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from motor import Motor
from presets_model import Preset, Table, now_str, parse_pasted_values, parse_pv_names

# Colours
C_OK = QColor(200, 255, 200)
C_MOVING = QColor(255, 245, 170)
C_BAD = QColor(255, 200, 200)
C_DISC = QColor(230, 230, 230)
C_LIVE = QColor(235, 240, 255)
C_NONE = QBrush()

COL_NAME = 0  # preset name
FIRST_MOTOR_COL = 1
LIVE_ROW = 0


class _Table(QTableWidget):
    """QTableWidget with Ctrl+V paste and Delete handling delegated."""

    pasteRequested = pyqtSignal()
    deleteRequested = pyqtSignal()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Paste):
            self.pasteRequested.emit()
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and not self.state() == QAbstractItemView.EditingState:
            self.deleteRequested.emit()
            return
        super().keyPressEvent(event)


class TableWidget(QWidget):
    """GUI for one :class:`presets_model.Table`."""

    changed = pyqtSignal()  # model modified -> main window saves
    status = pyqtSignal(str)

    def __init__(self, table: Table, parent: typing.Optional[QWidget] = None):
        super().__init__(parent)
        self.model = table
        self.motors: list[Motor] = []
        # column index -> target of an in-flight move (for the yellow highlight)
        self._move_targets: dict[int, float] = {}
        self._building = False

        self._build_ui()
        for base in list(self.model.motors):
            self._create_motor(base)
        self._rebuild()

    # ================================================================ UI
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)

        # --- motor bar
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Motor PV:"))
        self.pv_edit = QLineEdit()
        self.pv_edit.setPlaceholderText("e.g. MFX:DG1:MMS:01  (several allowed, space/comma separated)")
        self.pv_edit.returnPressed.connect(self._on_add_motor)
        bar.addWidget(self.pv_edit, 1)
        b = QPushButton("Add motor(s)")
        b.clicked.connect(self._on_add_motor)
        bar.addWidget(b)
        b = QPushButton("Paste PV names")
        b.setToolTip("Add every PV name found in the clipboard as a motor column")
        b.clicked.connect(self._on_paste_pvs)
        bar.addWidget(b)
        bar.addSpacing(20)
        bar.addWidget(QLabel("Tolerance:"))
        self.tol_spin = QDoubleSpinBox()
        self.tol_spin.setDecimals(4)
        self.tol_spin.setRange(0.0, 1e6)
        self.tol_spin.setSingleStep(0.001)
        self.tol_spin.setSpecialValueText("motor RDBD")
        self.tol_spin.setToolTip("A cell is green when |readback - value| <= tolerance.\n0 = use each motor's RDBD field.")
        self.tol_spin.setValue(self.model.tolerance or 0.0)
        self.tol_spin.valueChanged.connect(self._on_tol_changed)
        bar.addWidget(self.tol_spin)
        lay.addLayout(bar)

        # --- table
        self.table = _Table()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionDoubleClicked.connect(self._on_header_double_clicked)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._header_menu)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._cell_menu)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.pasteRequested.connect(self._on_paste_values)
        self.table.deleteRequested.connect(self._on_delete_preset)
        self.table.itemDoubleClicked.connect(self._on_double_clicked)
        lay.addWidget(self.table, 1)

        # --- buttons
        btns = QHBoxLayout()
        self.btn_capture = QPushButton("Capture current")
        self.btn_capture.setToolTip("Add a new preset row with the current readback of every motor")
        self.btn_capture.clicked.connect(self._on_capture)
        btns.addWidget(self.btn_capture)
        self.btn_update = QPushButton("Update selected from live")
        self.btn_update.setToolTip("Overwrite the selected preset(s) with the current readbacks")
        self.btn_update.clicked.connect(self._on_update_selected)
        btns.addWidget(self.btn_update)
        self.btn_paste = QPushButton("Paste values")
        self.btn_paste.setToolTip(
            "Paste rows of numbers from the clipboard (tab / comma / space separated).\n"
            "If a motor cell is selected, pasting starts there; otherwise new rows are added.\n"
            "A leading non-numeric cell is used as the preset name."
        )
        self.btn_paste.clicked.connect(self._on_paste_values)
        btns.addWidget(self.btn_paste)
        self.btn_new = QPushButton("New empty row")
        self.btn_new.clicked.connect(self._on_new_row)
        btns.addWidget(self.btn_new)
        self.btn_delete = QPushButton("Delete row(s)")
        self.btn_delete.clicked.connect(self._on_delete_preset)
        btns.addWidget(self.btn_delete)
        btns.addStretch(1)
        self.btn_restore = QPushButton("Move to selected  ▶")
        self.btn_restore.setStyleSheet("font-weight: bold;")
        self.btn_restore.setToolTip("Move every motor to the selected preset immediately")
        self.btn_restore.clicked.connect(self._on_move)
        btns.addWidget(self.btn_restore)
        lay.addLayout(btns)

    # ================================================================ motors
    def _create_motor(self, base: str) -> Motor:
        m = Motor(base, self)
        m.connectionChanged.connect(lambda c, m=m: self._on_motor_conn(m, c))
        m.readbackChanged.connect(lambda v, m=m: self._on_motor_rbv(m, v))
        m.movingChanged.connect(lambda mv, m=m: self._on_motor_moving(m, mv))
        m.infoChanged.connect(lambda m=m: self._on_motor_info(m))
        self.motors.append(m)
        m.connect()
        return m

    def _motor_col(self, m: Motor) -> int:
        return FIRST_MOTOR_COL + self.motors.index(m)

    def _col_motor(self, col: int) -> typing.Optional[Motor]:
        i = col - FIRST_MOTOR_COL
        if 0 <= i < len(self.motors):
            return self.motors[i]
        return None

    def _on_add_motor(self) -> None:
        names = parse_pv_names(self.pv_edit.text())
        if not names:
            return
        self.pv_edit.clear()
        self._add_motors(names)

    def _on_paste_pvs(self) -> None:
        names = parse_pv_names(QApplication.clipboard().text())
        if not names:
            self.status.emit("Clipboard contains no PV names")
            return
        self._add_motors(names)

    def _add_motors(self, names: list[str]) -> None:
        added = 0
        for n in names:
            if n in self.model.motors:
                continue
            self.model.add_motor(n)
            self._create_motor(n)
            added += 1
        if added:
            self._rebuild()
            self.changed.emit()
            self.status.emit(f"Added {added} motor(s); waiting for connection...")

    def _remove_motor(self, col: int) -> None:
        m = self._col_motor(col)
        if m is None:
            return
        if (
            QMessageBox.question(
                self,
                "Remove motor",
                f"Remove column {m.base} and its values from every preset?",
            )
            != QMessageBox.Yes
        ):
            return
        idx = self.motors.index(m)
        m.disconnect()
        m.deleteLater()
        del self.motors[idx]
        self.model.remove_motor(idx)
        self._rebuild()
        self.changed.emit()

    def _move_motor(self, col: int, delta: int) -> None:
        src = col - FIRST_MOTOR_COL
        dst = src + delta
        if not (0 <= src < len(self.motors) and 0 <= dst < len(self.motors)):
            return
        self.motors.insert(dst, self.motors.pop(src))
        self.model.move_motor(src, dst)
        self._rebuild()
        self.changed.emit()

    def shutdown(self) -> None:
        for m in self.motors:
            m.disconnect()

    # ------------------------------------------------ motor signal slots
    def _on_motor_conn(self, m: Motor, connected: bool) -> None:
        if m not in self.motors:
            return
        self._update_header(m)
        self._update_live_cell(m)
        self._recolour_column(m)
        self.status.emit(f"{self._label(m)} {'connected' if connected else 'DISCONNECTED'}")

    def _on_motor_rbv(self, m: Motor, value: float) -> None:
        if m not in self.motors:
            return
        self._update_live_cell(m)
        self._recolour_column(m)

    def _on_motor_moving(self, m: Motor, moving: bool) -> None:
        if m not in self.motors:
            return
        if not moving:
            self._move_targets.pop(self._motor_col(m), None)
        self._update_live_cell(m)
        self._recolour_column(m)

    def _on_motor_info(self, m: Motor) -> None:
        if m not in self.motors:
            return
        self._update_header(m)
        self._update_live_cell(m)
        # re-format all preset cells with the motor's precision
        col = self._motor_col(m)
        self._building = True
        try:
            for r, p in enumerate(self.model.presets, start=1):
                item = self.table.item(r, col)
                if item is not None:
                    item.setText(m.fmt(p.values[col - FIRST_MOTOR_COL]))
        finally:
            self._building = False

    # ================================================================ table
    def _rebuild(self) -> None:
        """Rebuild the whole QTableWidget from the model."""
        self._building = True
        try:
            self.table.clear()
            ncols = FIRST_MOTOR_COL + len(self.motors) + 1  # + note column
            self.table.setColumnCount(ncols)
            headers = ["Preset"] + [m.base for m in self.motors] + ["Note"]
            self.table.setHorizontalHeaderLabels(headers)
            self.table.setRowCount(1 + len(self.model.presets))

            # LIVE row
            bold = QFont()
            bold.setBold(True)
            item = QTableWidgetItem("LIVE")
            item.setFont(bold)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            item.setBackground(C_LIVE)
            self.table.setItem(LIVE_ROW, COL_NAME, item)
            for m in self.motors:
                item = QTableWidgetItem("")
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setFont(bold)
                self.table.setItem(LIVE_ROW, self._motor_col(m), item)
            item = QTableWidgetItem("current readback")
            item.setFlags(Qt.ItemIsEnabled)
            item.setBackground(C_LIVE)
            self.table.setItem(LIVE_ROW, ncols - 1, item)

            # preset rows
            for r, p in enumerate(self.model.presets, start=1):
                self._fill_preset_row(r, p)

            for m in self.motors:
                self._update_header(m)
                self._update_live_cell(m)
                self._recolour_column(m)
            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setStretchLastSection(True)
        finally:
            self._building = False

    def _fill_preset_row(self, row: int, p: Preset) -> None:
        name = QTableWidgetItem(p.name)
        name.setToolTip(f"saved {p.saved}")
        self.table.setItem(row, COL_NAME, name)
        for i, m in enumerate(self.motors):
            item = QTableWidgetItem(m.fmt(p.values[i]))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, FIRST_MOTOR_COL + i, item)
        note = QTableWidgetItem(p.note)
        self.table.setItem(row, FIRST_MOTOR_COL + len(self.motors), note)

    def _label(self, m: Motor) -> str:
        return self.model.label(self.motors.index(m))

    def _update_header(self, m: Motor) -> None:
        col = self._motor_col(m)
        idx = self.motors.index(m)
        alias = self.model.aliases[idx]
        mark = "●" if m.connected else "○"
        text = f"{mark} {alias or m.base}"
        if m.egu:
            text += f" [{m.egu}]"
        if alias:
            text += f"\n{m.base}"
        elif m.desc:
            text += f"\n{m.desc}"
        hdr = QTableWidgetItem(text)
        tip = m.tooltip()
        if alias:
            tip = f"{alias}\n{tip}"
        hdr.setToolTip(tip + "\n\nDouble-click to rename column")
        hdr.setForeground(QColor("darkgreen") if m.connected else QColor("red"))
        self.table.setHorizontalHeaderItem(col, hdr)

    def _update_live_cell(self, m: Motor) -> None:
        item = self.table.item(LIVE_ROW, self._motor_col(m))
        if item is None:
            return
        was = self._building
        self._building = True
        try:
            if not m.connected:
                item.setText("disconnected")
                item.setBackground(C_BAD)
            else:
                item.setText(m.fmt(m.readback))
                item.setBackground(C_MOVING if m.moving else C_OK)
            item.setToolTip(m.tooltip())
        finally:
            self._building = was

    def _recolour_column(self, m: Motor) -> None:
        """
        Colour every preset cell of this motor from the live readback:
        green = at that value (within tolerance), yellow = moving toward it,
        grey = motor disconnected, red = outside soft limits.
        """
        col = self._motor_col(m)
        idx = col - FIRST_MOTOR_COL
        target = self._move_targets.get(col)
        tol = self._tol()
        for r, p in enumerate(self.model.presets, start=1):
            item = self.table.item(r, col)
            if item is None:
                continue
            v = p.values[idx]
            if v is None:
                self._set_bg(item, C_NONE)
            elif not m.in_limits(v):
                self._set_bg(item, C_BAD, "outside motor soft limits")
            elif not m.connected:
                self._set_bg(item, C_DISC)
            elif m.moving and target is not None and abs(target - v) <= 1e-12:
                self._set_bg(item, C_MOVING)
            elif not m.moving and m.at_target(v, tol):
                self._set_bg(item, C_OK)
            else:
                self._set_bg(item, C_NONE)

    def _recolour_all(self) -> None:
        for m in self.motors:
            self._recolour_column(m)

    def _tol(self) -> typing.Optional[float]:
        t = self.model.tolerance
        return None if not t else t

    # -------------------------------------------------------- edits
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._building:
            return
        row, col = item.row(), item.column()
        if row == LIVE_ROW:
            return
        pidx = row - 1
        if pidx >= len(self.model.presets):
            return
        p = self.model.presets[pidx]
        if col == COL_NAME:
            p.name = item.text().strip() or p.name
            if item.text().strip() != p.name:
                self._set_text_silent(item, p.name)
        elif col == FIRST_MOTOR_COL + len(self.motors):
            p.note = item.text()
        else:
            m = self._col_motor(col)
            if m is None:
                return
            txt = item.text().strip()
            if txt == "":
                p.values[col - FIRST_MOTOR_COL] = None
            else:
                try:
                    v = float(txt)
                except ValueError:
                    self._set_text_silent(item, m.fmt(p.values[col - FIRST_MOTOR_COL]))
                    self.status.emit(f"'{txt}' is not a number")
                    return
                p.values[col - FIRST_MOTOR_COL] = v
                self._set_text_silent(item, m.fmt(v))
            self._recolour_column(m)
        p.saved = now_str()
        name_item = self.table.item(row, COL_NAME)
        if name_item is not None:
            self._building = True
            try:
                name_item.setToolTip(f"saved {p.saved}")
            finally:
                self._building = False
        self.changed.emit()

    def _set_text_silent(self, item: QTableWidgetItem, text: str) -> None:
        self._building = True
        try:
            item.setText(text)
        finally:
            self._building = False

    def _set_bg(self, item: QTableWidgetItem, brush, tooltip: typing.Optional[str] = None) -> None:
        """setBackground without triggering itemChanged handling."""
        was = self._building
        self._building = True
        try:
            item.setBackground(brush)
            if tooltip is not None:
                item.setToolTip(tooltip)
        finally:
            self._building = was

    def _on_double_clicked(self, item: QTableWidgetItem) -> None:
        # Double-click a LIVE motor cell -> copy its value to clipboard
        if item.row() == LIVE_ROW and self._col_motor(item.column()) is not None:
            QApplication.clipboard().setText(item.text())
            self.status.emit(f"Copied {item.text()} to clipboard")

    # -------------------------------------------------------- rows
    def _selected_preset_indices(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()} - {LIVE_ROW})
        return [r - 1 for r in rows if 0 <= r - 1 < len(self.model.presets)]

    def _live_values(self, fresh: bool = True) -> list[typing.Optional[float]]:
        vals = []
        for m in self.motors:
            if not m.connected:
                vals.append(None)
            else:
                vals.append(m.read_fresh(0.5) if fresh else m.readback)
        return vals

    def _append_preset(self, name: str, values, note: str = "") -> int:
        self.model.add_preset(name, values, note)
        self._rebuild()
        self.changed.emit()
        row = len(self.model.presets)
        self.table.selectRow(row)
        return row

    def _on_capture(self) -> None:
        if not self.motors:
            self.status.emit("Add at least one motor first")
            return
        name, ok = QInputDialog.getText(
            self, "Capture current position", "Preset name:", text=self.model.unique_preset_name("Position")
        )
        if not ok or not name.strip():
            return
        self._append_preset(name.strip(), self._live_values())
        self.status.emit(f"Captured '{name.strip()}'")

    def _on_update_selected(self) -> None:
        idxs = self._selected_preset_indices()
        if not idxs:
            self.status.emit("Select a preset row first")
            return
        if len(idxs) > 1 and (
            QMessageBox.question(self, "Update presets", f"Overwrite {len(idxs)} presets with the live readbacks?")
            != QMessageBox.Yes
        ):
            return
        live = self._live_values()
        for i in idxs:
            p = self.model.presets[i]
            for k, v in enumerate(live):
                if v is not None:
                    p.values[k] = v
            p.saved = now_str()
        self._rebuild()
        self.changed.emit()

    def _on_new_row(self) -> None:
        self._append_preset(self.model.unique_preset_name(), [None] * len(self.motors))

    def _on_delete_preset(self) -> None:
        idxs = self._selected_preset_indices()
        if not idxs:
            return
        names = ", ".join(self.model.presets[i].name for i in idxs)
        if QMessageBox.question(self, "Delete preset(s)", f"Delete: {names}?") != QMessageBox.Yes:
            return
        for i in reversed(idxs):
            self.model.remove_preset(i)
        self._rebuild()
        self.changed.emit()

    def _move_preset(self, idx: int, delta: int) -> None:
        dst = idx + delta
        if not (0 <= dst < len(self.model.presets)):
            return
        self.model.move_preset(idx, dst)
        self._rebuild()
        self.table.selectRow(dst + 1)
        self.changed.emit()

    def _on_paste_values(self) -> None:
        rows = parse_pasted_values(QApplication.clipboard().text())
        if not rows:
            self.status.emit("Clipboard has no values")
            return
        nm = len(self.motors)
        cur = self.table.currentItem()
        start_row = start_col = None
        if cur is not None and cur.row() != LIVE_ROW and self._col_motor(cur.column()) is not None:
            start_row, start_col = cur.row() - 1, cur.column() - FIRST_MOTOR_COL

        if start_row is None:
            # Append new preset rows. First cell may be a name.
            for cells in rows:
                name = None
                if cells and cells[0] is not None and not _is_number(cells[0]):
                    name = cells[0]
                    cells = cells[1:]
                vals = [_to_float(c) for c in cells][:nm]
                self.model.add_preset(name or self.model.unique_preset_name("Pasted"), vals)
            self._rebuild()
            self.changed.emit()
            self.status.emit(f"Added {len(rows)} preset row(s) from clipboard")
            return

        # Overwrite starting at the current cell; grow rows as required.
        for r, cells in enumerate(rows):
            vals = [_to_float(c) for c in cells]
            if cells and cells[0] is not None and not _is_number(cells[0]):
                vals = vals[1:]
            pidx = start_row + r
            while pidx >= len(self.model.presets):
                self.model.add_preset(self.model.unique_preset_name("Pasted"), [None] * nm)
            p = self.model.presets[pidx]
            for k, v in enumerate(vals):
                col = start_col + k
                if col < nm and v is not None:
                    p.values[col] = v
            p.saved = now_str()
        self._rebuild()
        self.changed.emit()
        self.status.emit("Pasted values")

    def _on_tol_changed(self, v: float) -> None:
        self.model.tolerance = None if v <= 0 else v
        self._recolour_all()
        self.changed.emit()

    def _on_header_double_clicked(self, col: int) -> None:
        self._rename_motor(col)

    def _rename_motor(self, col: int) -> None:
        m = self._col_motor(col)
        if m is None:
            return
        idx = self.motors.index(m)
        name, ok = QInputDialog.getText(
            self,
            "Rename column",
            f"Friendly name for {m.base}\n(leave empty to show the PV name):",
            text=self.model.aliases[idx] or "",
        )
        if not ok:
            return
        self.model.set_alias(idx, name)
        self._update_header(m)
        self.table.resizeColumnToContents(col)
        self.changed.emit()

    # -------------------------------------------------------- menus
    def _header_menu(self, pos) -> None:
        col = self.table.horizontalHeader().logicalIndexAt(pos)
        m = self._col_motor(col)
        if m is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename column...", lambda: self._rename_motor(col))
        menu.addAction(f"Copy PV name  ({m.base})", lambda: QApplication.clipboard().setText(m.base))
        menu.addAction("Reconnect", lambda: (m.disconnect(), m.connect()))
        menu.addSeparator()
        menu.addAction("Move left", lambda: self._move_motor(col, -1))
        menu.addAction("Move right", lambda: self._move_motor(col, +1))
        menu.addSeparator()
        menu.addAction("Remove motor column", lambda: self._remove_motor(col))
        menu.exec_(self.table.horizontalHeader().mapToGlobal(pos))

    def _cell_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        menu = QMenu(self)
        if item is not None and item.row() != LIVE_ROW:
            idx = item.row() - 1
            menu.addAction("Move to this preset", self._on_move)
            menu.addAction("Update from live", self._on_update_selected)
            menu.addSeparator()
            menu.addAction("Duplicate row", lambda: self._duplicate(idx))
            menu.addAction("Move up", lambda: self._move_preset(idx, -1))
            menu.addAction("Move down", lambda: self._move_preset(idx, +1))
            menu.addSeparator()
            menu.addAction("Delete row(s)", self._on_delete_preset)
            menu.addSeparator()
        menu.addAction("Paste values here", self._on_paste_values)
        menu.addAction("Copy table as text", self._copy_table)
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _duplicate(self, idx: int) -> None:
        p = self.model.presets[idx]
        self.model.add_preset(self.model.unique_preset_name(p.name), list(p.values), p.note)
        self._rebuild()
        self.changed.emit()

    def _copy_table(self) -> None:
        lines = ["\t".join(["Preset"] + [self._label(m) for m in self.motors] + ["Note"])]
        for p in self.model.presets:
            lines.append("\t".join([p.name] + [("" if v is None else repr(v)) for v in p.values] + [p.note]))
        QApplication.clipboard().setText("\n".join(lines))
        self.status.emit("Table copied to clipboard")

    # ================================================================ move
    def _on_move(self) -> None:
        idxs = self._selected_preset_indices()
        if len(idxs) != 1:
            self.status.emit("Select exactly one preset row to move to")
            return
        idx = idxs[0]
        p = self.model.presets[idx]

        moves: list[tuple[Motor, float]] = []
        skipped: list[str] = []
        for m, target in zip(self.motors, p.values):
            if target is None:
                continue  # empty cell: nothing to do, not worth a warning
            label = self._label(m)
            if not m.connected:
                skipped.append(f"{label}: disconnected")
            elif not m.write_access:
                skipped.append(f"{label}: no write access")
            elif not m.in_limits(target):
                skipped.append(f"{label}: {m.fmt(target)} outside soft limits {m.fmt(m.llm)}..{m.fmt(m.hlm)}")
            else:
                moves.append((m, target))

        n = 0
        for m, target in moves:
            if m.move(target):
                n += 1
                self._move_targets[self._motor_col(m)] = target
            else:
                skipped.append(f"{self._label(m)}: put to {m.base}.VAL failed")
        self.status.emit(f"Moving to '{p.name}': {n} motor(s) commanded")
        self._recolour_all()
        # A motor already at its target never toggles DMOV; recheck shortly.
        QTimer.singleShot(500, self._recolour_all)

        if skipped:
            QMessageBox.warning(
                self,
                "Some motors were not moved",
                f"Moving to '{p.name}': {n} motor(s) commanded.\n\nSkipped:\n  " + "\n  ".join(skipped),
            )


def _is_number(s: typing.Optional[str]) -> bool:
    if s is None:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _to_float(s: typing.Optional[str]) -> typing.Optional[float]:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None
