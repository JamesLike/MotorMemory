"""
Main window: one dockable panel per preset table, autosave, status bar.

Each table lives in a QDockWidget.  Panels start tabbed together; drag a
panel's title bar to float it as its own window (e.g. onto a second monitor)
or drop it next to another panel to tile them.  The layout is saved in
QSettings and restored on the next start.
"""

from __future__ import annotations

import os
import typing

from PyQt5.QtCore import QSettings, Qt, QTimer
from PyQt5.QtWidgets import (
    QAction,
    QCheckBox,
    QDockWidget,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QToolBar,
    QWidget,
)

import pv_backend
from elog_poster import ElogPoster, format_changes
from presets_model import PresetStore, Table
from table_widget import TableWidget

SETTINGS = ("SLAC", "MotorMemory")


class TableDock(QDockWidget):
    """Dock widget holding one TableWidget; double-click the title to rename."""

    renameRequested = None  # set by MainWindow as a callable

    def __init__(self, table: Table, parent: QWidget):
        super().__init__(table.name, parent)
        self.setObjectName(f"table_{table.uid}")
        self.setFeatures(
            QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable
        )
        self.setAllowedAreas(Qt.AllDockWidgetAreas)
        self.view = TableWidget(table)
        self.setWidget(self.view)

    @property
    def model(self) -> Table:
        return self.view.model

    def closeEvent(self, event):
        # Closing a floating panel returns it to the main window instead of
        # hiding it; closing a docked panel hides it (Tables menu re-shows).
        if self.isFloating():
            event.ignore()
            self.setFloating(False)
            self.show()
            self.raise_()
            return
        super().closeEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Double-click on the title bar (not inside the table) -> rename
        if event.pos().y() < self.widget().geometry().top() and callable(self.renameRequested):
            self.renameRequested(self)
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    EDIT_BATCH_MS = 5000

    def __init__(self, store: PresetStore, hutch: typing.Optional[str] = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(f"MotorMemory - {os.path.abspath(store.path)}")
        self.docks: list[TableDock] = []
        self._active: typing.Optional[TableDock] = None

        # eLog
        self.elog = ElogPoster(hutch, self)
        self.elog.posted.connect(lambda t: self.statusBar().showMessage(f"eLog: posted '{t}'", 5000))
        self.elog.connected.connect(lambda d: self.statusBar().showMessage(f"eLog: connected to {d}", 5000))
        self.elog.failed.connect(self._on_elog_failed)
        self._elog_warned = False
        # table uid -> list of (what, old, new) edits waiting to be posted
        self._edit_batches: dict[str, list[list[str]]] = {}
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(self.EDIT_BATCH_MS)
        self._edit_timer.timeout.connect(self._flush_edits)

        # Docking behaviour: nested splits + tab groups, animated
        self.setDockOptions(
            QMainWindow.AnimatedDocks
            | QMainWindow.AllowNestedDocks
            | QMainWindow.AllowTabbedDocks
            | QMainWindow.GroupedDragging
        )
        self.setTabPosition(Qt.AllDockWidgetAreas, QTabWidget.North)
        # No central widget: the docks fill the window
        central = QWidget()
        central.setFixedSize(0, 0)
        self.setCentralWidget(central)

        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)
        a = QAction("New table", self)
        a.triggered.connect(self._new_table)
        tb.addAction(a)
        a = QAction("Rename table", self)
        a.triggered.connect(lambda: self._rename_table(self._current_dock()))
        tb.addAction(a)
        a = QAction("Delete table", self)
        a.triggered.connect(self._delete_table)
        tb.addAction(a)
        tb.addSeparator()
        self.view_menu = QMenu("Tables", self)
        a = QAction("Tables ▾", self)
        a.setToolTip("Show / hide tables")
        a.setMenu(self.view_menu)
        a.triggered.connect(lambda: self.view_menu.exec_(tb.mapToGlobal(tb.actionGeometry(a).bottomLeft())))
        tb.addAction(a)
        self.view_menu.aboutToShow.connect(self._fill_view_menu)
        a = QAction("Tile all", self)
        a.setToolTip("Show every table side by side in this window")
        a.triggered.connect(self._tile_all)
        tb.addAction(a)
        a = QAction("Stack as tabs", self)
        a.setToolTip("Put every table back into one tab group")
        a.triggered.connect(self._tab_all)
        tb.addAction(a)
        tb.addSeparator()
        a = QAction("Save now", self)
        a.setShortcut("Ctrl+S")
        a.triggered.connect(self.save)
        tb.addAction(a)
        a = QAction("Reload from disk", self)
        a.triggered.connect(self._reload)
        tb.addAction(a)
        tb.addSeparator()
        self.elog_chk = QCheckBox("Post to eLog")
        self.elog_chk.setToolTip(
            "Post moves (after the motors settle) and preset/table changes to the experiment eLog.\n"
            "Uses the Kerberos ticket of the account running MotorMemory."
        )
        if not self.elog.available:
            self.elog_chk.setEnabled(False)
            self.elog_chk.setToolTip(f"eLog posting unavailable: {self.elog.unavailable_reason}")
        else:
            self.elog_chk.setChecked(bool(QSettings(*SETTINGS).value("elog", False, type=bool)))
        self.elog_chk.toggled.connect(self._on_elog_toggled)
        self._on_elog_toggled(self.elog_chk.isChecked())
        tb.addWidget(self.elog_chk)
        tb.addSeparator()
        a = QAction("Help", self)
        a.triggered.connect(self._help)
        tb.addAction(a)

        self.statusBar().showMessage(f"CA backend: {pv_backend.BACKEND or 'NONE (no EPICS library found)'}")

        # Debounced autosave
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.save)

        self._populate()

        settings = QSettings(*SETTINGS)
        geo = settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1000, 500)
        state = settings.value("windowState")
        if state is not None:
            self.restoreState(state)
        # A table that was hidden when we last quit is still closed; make sure
        # at least one is visible so the window is not empty.
        if self.docks and not any(d.isVisible() for d in self.docks):
            self.docks[0].show()

    # ------------------------------------------------------------ docks
    def _populate(self) -> None:
        for d in self.docks:
            self.removeDockWidget(d)
            d.view.shutdown()
            d.deleteLater()
        self.docks = []
        self._active = None
        if not self.store.tables:
            self.store.add_table("Table 1")
        for t in self.store.tables:
            self._add_dock(t)

    def _add_dock(self, table: Table) -> TableDock:
        d = TableDock(table, self)
        d.renameRequested = self._rename_table
        d.view.changed.connect(self._schedule_save)
        d.view.status.connect(lambda msg: self.statusBar().showMessage(msg, 5000))
        d.view.logEvent.connect(lambda summary, headers, rows, d=d: self._post_event(d.model, summary, headers, rows))
        d.view.edit.connect(lambda what, old, new, d=d: self._queue_edit(d.model, what, old, new))
        d.view.movePost.connect(lambda title, body, d=d: self._post(title, body, d.model.name))
        for base in table.motors:
            self.elog.set_hutch_from_pv(base)
        d.visibilityChanged.connect(lambda vis, d=d: vis and self._set_active(d))
        d.view.table.itemSelectionChanged.connect(lambda d=d: self._set_active(d))
        d.view.pv_edit.textEdited.connect(lambda _t, d=d: self._set_active(d))
        self.addDockWidget(Qt.TopDockWidgetArea, d)
        if self.docks:
            self.tabifyDockWidget(self.docks[-1], d)
        self.docks.append(d)
        return d

    def _set_active(self, d: TableDock) -> None:
        self._active = d

    def _current_dock(self) -> typing.Optional[TableDock]:
        """The table the user most recently interacted with (or the visible one)."""
        if self._active is not None and self._active in self.docks:
            return self._active
        for d in self.docks:
            if d.isVisible() and not d.visibleRegion().isEmpty():
                return d
        return self.docks[0] if self.docks else None

    def _new_table(self) -> None:
        name, ok = QInputDialog.getText(self, "New table", "Table name:", text=self.store.unique_table_name())
        if not ok or not name.strip():
            return
        t = self.store.add_table(name.strip())
        d = self._add_dock(t)
        d.show()
        d.raise_()
        self._set_active(d)
        self._schedule_save()
        self._post_event(t, "table created", [], [])

    def _rename_table(self, d: typing.Optional[TableDock]) -> None:
        if d is None:
            return
        name, ok = QInputDialog.getText(self, "Rename table", "Table name:", text=d.model.name)
        if not ok or not name.strip():
            return
        old = d.model.name
        d.model.name = name.strip()
        d.setWindowTitle(d.model.name)
        self._schedule_save()
        if d.model.name != old:
            self._post_event(d.model, f"table renamed from '{old}'", [], [])

    def _delete_table(self) -> None:
        d = self._current_dock()
        if d is None:
            return
        if (
            QMessageBox.question(
                self,
                "Delete table",
                f"Delete table '{d.model.name}' with {len(d.model.presets)} preset(s)?",
            )
            != QMessageBox.Yes
        ):
            return
        rows = [[p.name] + [m.fmt(v) for m, v in zip(d.view.motors, p.values)] for p in d.model.presets]
        headers = ["Preset"] + [d.model.label(i) for i in range(len(d.model.motors))]
        self._post_event(d.model, f"table deleted ({len(d.model.presets)} presets)", headers, rows)
        self.removeDockWidget(d)
        self.docks.remove(d)
        d.view.shutdown()
        d.deleteLater()
        self.store.remove_table(d.model)
        self._active = None
        if not self.docks:
            self._add_dock(self.store.add_table("Table 1")).show()
        self._schedule_save()

    def _fill_view_menu(self) -> None:
        self.view_menu.clear()
        for d in self.docks:
            a = self.view_menu.addAction(d.model.name)
            a.setCheckable(True)
            a.setChecked(d.isVisible())
            a.toggled.connect(lambda on, d=d: (d.setVisible(on), on and d.raise_()))
        self.view_menu.addSeparator()
        self.view_menu.addAction("Show all", lambda: [d.show() for d in self.docks])

    def _tile_all(self) -> None:
        """Lay every table out side by side (horizontally) in the main window."""
        for d in self.docks:
            d.setFloating(False)
            d.show()
        if not self.docks:
            return
        # Re-add in order to the same area: each addDockWidget after the first
        # splits horizontally, giving a row of panels.
        for d in self.docks:
            self.removeDockWidget(d)
        for d in self.docks:
            self.addDockWidget(Qt.TopDockWidgetArea, d, Qt.Horizontal)
            d.show()
        w = max(1, self.width() // len(self.docks))
        self.resizeDocks(self.docks, [w] * len(self.docks), Qt.Horizontal)

    def _tab_all(self) -> None:
        """Put every table back into a single tab group."""
        for d in self.docks:
            d.setFloating(False)
            d.show()
        for i, d in enumerate(self.docks):
            if i == 0:
                continue
            self.tabifyDockWidget(self.docks[0], d)
        if self.docks:
            self.docks[0].raise_()

    def _reload(self) -> None:
        if (
            QMessageBox.question(self, "Reload", "Discard unsaved edits and reload presets from disk?")
            != QMessageBox.Yes
        ):
            return
        try:
            self.store.load()
        except Exception as exc:
            QMessageBox.critical(self, "Reload failed", str(exc))
            return
        self._populate()
        self.statusBar().showMessage("Reloaded", 3000)

    # ------------------------------------------------------------ eLog
    def _on_elog_toggled(self, on: bool) -> None:
        self.elog.enabled = bool(on)
        QSettings(*SETTINGS).setValue("elog", bool(on))
        if on and self.elog.available:
            if not self.elog.hutch:
                for t in self.store.tables:
                    for base in t.motors:
                        self.elog.set_hutch_from_pv(base)
            self.statusBar().showMessage(
                f"eLog posting ON ({self.elog.hutch or 'hutch unknown - add a motor or use --hutch'})", 5000
            )
        elif not on:
            self.statusBar().showMessage("eLog posting off", 3000)

    def _post(self, title: str, body: str, table_name: str) -> None:
        if not self.elog.hutch:
            for t in self.store.tables:
                for base in t.motors:
                    self.elog.set_hutch_from_pv(base)
        self.elog.post(title, body, [table_name.replace(" ", "_")])

    def _post_event(self, table: Table, summary: str, headers: list, rows: list) -> None:
        if not self.elog.enabled:
            return
        title, body = format_changes(table.name, summary, rows, headers)
        self._post(title, body, table.name)

    def _queue_edit(self, table: Table, what: str, old: str, new: str) -> None:
        if not self.elog.enabled:
            return
        self._edit_batches.setdefault(table.uid, []).append([what, old, new])
        self._edit_timer.start()

    def _flush_edits(self) -> None:
        batches, self._edit_batches = self._edit_batches, {}
        by_uid = {t.uid: t for t in self.store.tables}
        for uid, edits in batches.items():
            t = by_uid.get(uid)
            if t is None or not edits:
                continue
            n = len(edits)
            title, body = format_changes(t.name, f"{n} edit{'s' if n != 1 else ''}", edits, ["What", "Old", "New"])
            self._post(title, body, t.name)

    def _on_elog_failed(self, title: str, err: str) -> None:
        self.statusBar().showMessage(f"eLog post FAILED: {err}", 10000)
        print(f"eLog post failed ('{title}'): {err}")
        if not self._elog_warned:
            self._elog_warned = True
            hint = "\n\nIf this is an authentication error, run 'kinit' in a terminal." if "401" in err or "auth" in err.lower() or "kerberos" in err.lower() else ""
            QMessageBox.warning(self, "eLog post failed", f"Could not post '{title}':\n{err}{hint}\n\nFurther failures are shown in the status bar only.")

    # ------------------------------------------------------------ saving
    def _schedule_save(self) -> None:
        self._save_timer.start()

    def save(self) -> None:
        try:
            self.store.save()
            self.statusBar().showMessage(f"Saved {self.store.path}", 3000)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", f"Could not write {self.store.path}:\n{exc}")

    def closeEvent(self, event) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self.save()
        settings = QSettings(*SETTINGS)
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())
        if self._edit_timer.isActive():
            self._edit_timer.stop()
            self._flush_edits()
        self.elog.shutdown()
        for d in self.docks:
            d.view.shutdown()
        super().closeEvent(event)

    def _help(self) -> None:
        QMessageBox.information(
            self,
            "MotorMemory - help",
            "<b>Columns</b> are EPICS motor records, <b>rows</b> are named positions.<br><br>"
            "1. Type or paste motor base PVs (e.g. MFX:DG1:MMS:01) and press Add. "
            "The header turns green when connected; the LIVE row shows the readback.<br>"
            "2. <b>Capture current</b> adds a row with the live positions; "
            "<b>Paste values</b> adds rows from the clipboard "
            "(optionally with a name in the first column); or type values into cells.<br>"
            "3. Select a row and press <b>Move to selected</b>; motors move immediately. "
            "Cells are green when the motor is at that value, yellow while moving toward it.<br><br>"
            "<b>Tables</b> are dockable panels: drag a title bar to float a table as its own window "
            "or drop it beside another to tile them (closing a floating table re-docks it); <b>Tile all</b> / <b>Stack as tabs</b> rearrange "
            "everything; the <b>Tables</b> menu shows hidden ones. Double-click a title bar to rename. "
            "The layout is remembered.<br><br>"
            "Tick <b>Post to eLog</b> to log every move (once the motors settle, with from/target/final) "
            "and every preset or table change to the experiment eLog. Typed edits are grouped into one "
            "post after a few seconds.<br><br>"
            "Double-click a column header to give a motor a friendly name. "
            "Right-click a column header to move/remove a motor; right-click a row for more actions. "
            "Ctrl+V pastes into the table; Delete removes selected rows. Everything autosaves.",
        )
