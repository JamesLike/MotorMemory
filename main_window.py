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

    def mouseDoubleClickEvent(self, event):
        # Double-click on the title bar (not inside the table) -> rename
        if event.pos().y() < self.widget().geometry().top() and callable(self.renameRequested):
            self.renameRequested(self)
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, store: PresetStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(f"MotorMemory - {os.path.abspath(store.path)}")
        self.docks: list[TableDock] = []
        self._active: typing.Optional[TableDock] = None

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

    def _rename_table(self, d: typing.Optional[TableDock]) -> None:
        if d is None:
            return
        name, ok = QInputDialog.getText(self, "Rename table", "Table name:", text=d.model.name)
        if not ok or not name.strip():
            return
        d.model.name = name.strip()
        d.setWindowTitle(d.model.name)
        self._schedule_save()

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
            "or drop it beside another to tile them; <b>Tile all</b> / <b>Stack as tabs</b> rearrange "
            "everything; the <b>Tables</b> menu shows hidden ones. Double-click a title bar to rename. "
            "The layout is remembered.<br><br>"
            "Double-click a column header to give a motor a friendly name. "
            "Right-click a column header to move/remove a motor; right-click a row for more actions. "
            "Ctrl+V pastes into the table; Delete removes selected rows. Everything autosaves.",
        )
