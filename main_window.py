"""Main window: a tab per preset table, autosave, status bar."""

from __future__ import annotations

import os

from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import (
    QAction,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QToolBar,
)

import pv_backend
from presets_model import PresetStore
from table_widget import TableWidget


class MainWindow(QMainWindow):
    def __init__(self, store: PresetStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(f"MotorMemory - {os.path.abspath(store.path)}")

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(False)
        self.tabs.setMovable(True)
        self.tabs.tabBarDoubleClicked.connect(self._rename_table)
        self.tabs.tabBar().tabMoved.connect(self._on_tab_moved)
        self.setCentralWidget(self.tabs)

        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        a = QAction("New table", self)
        a.triggered.connect(self._new_table)
        tb.addAction(a)
        a = QAction("Rename table", self)
        a.triggered.connect(lambda: self._rename_table(self.tabs.currentIndex()))
        tb.addAction(a)
        a = QAction("Delete table", self)
        a.triggered.connect(self._delete_table)
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

        settings = QSettings("SLAC", "MotorMemory")
        geo = settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1000, 500)

    # ------------------------------------------------------------ tables
    def _populate(self) -> None:
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.shutdown()
            w.deleteLater()
        if not self.store.tables:
            self.store.add_table("Table 1")
        for t in self.store.tables:
            self._add_tab(t)

    def _add_tab(self, table) -> TableWidget:
        w = TableWidget(table)
        w.changed.connect(self._schedule_save)
        w.status.connect(lambda msg: self.statusBar().showMessage(msg, 5000))
        self.tabs.addTab(w, table.name)
        return w

    def _new_table(self) -> None:
        name, ok = QInputDialog.getText(self, "New table", "Table name:", text=self.store.unique_table_name())
        if not ok or not name.strip():
            return
        t = self.store.add_table(name.strip())
        w = self._add_tab(t)
        self.tabs.setCurrentWidget(w)
        self._schedule_save()

    def _rename_table(self, index: int) -> None:
        if index < 0:
            return
        w = self.tabs.widget(index)
        name, ok = QInputDialog.getText(self, "Rename table", "Table name:", text=w.model.name)
        if not ok or not name.strip():
            return
        w.model.name = name.strip()
        self.tabs.setTabText(index, w.model.name)
        self._schedule_save()

    def _delete_table(self) -> None:
        index = self.tabs.currentIndex()
        if index < 0:
            return
        w = self.tabs.widget(index)
        if (
            QMessageBox.question(
                self,
                "Delete table",
                f"Delete table '{w.model.name}' with {len(w.model.presets)} preset(s)?",
            )
            != QMessageBox.Yes
        ):
            return
        self.tabs.removeTab(index)
        w.shutdown()
        w.deleteLater()
        self.store.remove_table(w.model)
        if not self.tabs.count():
            self._add_tab(self.store.add_table("Table 1"))
        self._schedule_save()

    def _on_tab_moved(self, src: int, dst: int) -> None:
        # Keep store order in sync with the tab bar
        self.store.tables = [self.tabs.widget(i).model for i in range(self.tabs.count())]
        self._schedule_save()

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
        QSettings("SLAC", "MotorMemory").setValue("geometry", self.saveGeometry())
        for i in range(self.tabs.count()):
            self.tabs.widget(i).shutdown()
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
            "Double-click a column header to give a motor a friendly name. "
            "Right-click a column header to move/remove a motor; right-click a row for more actions. "
            "Ctrl+V pastes into the table; Delete removes selected rows. Everything autosaves.",
        )
