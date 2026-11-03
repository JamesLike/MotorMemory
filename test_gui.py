#!/usr/bin/env python3
"""
Headless smoke test for MotorMemory against sim_ioc.py.

    QT_QPA_PLATFORM=offscreen python test_gui.py

Requires the sim IOC to be running (./run_dev.sh --ioc).
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

import table_widget  # noqa: E402
import main_window  # noqa: E402
from main_window import MainWindow  # noqa: E402
from presets_model import PresetStore, parse_pasted_values, parse_pv_names  # noqa: E402
from table_widget import FIRST_MOTOR_COL, LIVE_ROW, C_NONE, C_OK  # noqa: E402


def spin(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        global failures
        failures += 1


failures = 0


def main():
    global failures
    # --- pure model tests
    check(parse_pv_names("SIM:MMS:01.RBV, SIM:MMS:02 SIM:MMS:01") == ["SIM:MMS:01", "SIM:MMS:02"], "parse_pv_names")
    check(parse_pasted_values("A\t1\t2\n# c\n3, 4\n5 6") == [["A", "1", "2"], ["3", "4"], ["5", "6"]], "parse_pasted_values")

    app = QApplication([])
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "presets.json")
    store = PresetStore(path)
    store.load()
    win = MainWindow(store)
    win.show()
    tw = win.docks[0].view

    # add motors
    tw.pv_edit.setText("SIM:MMS:01 SIM:MMS:02.RBV SIM:MMS:03")
    tw._on_add_motor()
    spin(app, 2.0)
    check(len(tw.motors) == 3, "3 motor columns created")
    check(all(m.connected for m in tw.motors), "all motors connected")
    check(tw.motors[0].desc == "sim x stage" and tw.motors[0].egu == "mm", f"DESC/EGU read ({tw.motors[0].desc!r})")
    live = tw.table.item(LIVE_ROW, FIRST_MOTOR_COL).text()
    check(live != "" and live != "disconnected", f"LIVE cell shows readback ({live})")

    # capture current (monkeypatch input dialog)
    table_widget.QInputDialog.getText = staticmethod(lambda *a, **k: ("Home", True))
    tw._on_capture()
    spin(app, 0.2)
    check(len(tw.model.presets) == 1 and tw.model.presets[0].name == "Home", "capture current created preset")

    # paste values as new rows
    app.clipboard().setText("PosA\t1.5\t-2.0\t0.25\n2.0\t2.0\t2.0")
    tw.table.setCurrentItem(None)
    tw._on_paste_values()
    spin(app, 0.2)
    check(len(tw.model.presets) == 3, "paste added 2 rows")
    check(tw.model.presets[1].values == [1.5, -2.0, 0.25], f"pasted values parsed {tw.model.presets[1].values}")
    check(tw.model.presets[2].name.startswith("Pasted"), "unnamed pasted row got default name")

    # edit a cell by hand
    item = tw.table.item(2, FIRST_MOTOR_COL + 2)
    item.setText("0.5")
    spin(app, 0.2)
    check(tw.model.presets[1].values[2] == 0.5, "cell edit updates model")
    item.setText("abc")
    spin(app, 0.2)
    check(tw.model.presets[1].values[2] == 0.5 and item.text() == "0.500", "invalid edit rejected")

    # park motors away from PosA so the restore really moves them
    for m in tw.motors:
        m.move(0.0)
    spin(app, 2.5)

    # "Home" row was captured at 0,0,0 => should be green now that motors are parked at 0
    home_cols = [tw.table.item(1, FIRST_MOTOR_COL + i).background().color().name() for i in range(3)]
    check(all(c == C_OK.name() for c in home_cols), f"row at live position is green {home_cols}")
    posa_cols = [tw.table.item(2, FIRST_MOTOR_COL + i).background() for i in range(3)]
    check(all(b.style() == C_NONE.style() for b in posa_cols), "row away from live position is uncoloured")

    # move to PosA: no confirmation dialog; any warning popup would be a failure
    warned = []
    QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a))
    tw.table.clearSelection()
    tw.table.selectRow(2)
    tw._on_move()
    spin(app, 0.3)
    check(any(m.moving for m in tw.motors), "motors moving after move (no confirm)")
    check(not warned, "no warning when all motors move")
    spin(app, 2.5)
    rb = [m.readback for m in tw.motors]
    check(
        all(abs(a - b) < 0.01 for a, b in zip(rb, [1.5, -2.0, 0.5])),
        f"motors reached PosA targets {rb}",
    )
    cols = [tw.table.item(2, FIRST_MOTOR_COL + i).background().color().name() for i in range(3)]
    check(all(c == C_OK.name() for c in cols), f"PosA cells now green {cols}")
    home_cols = [tw.table.item(1, FIRST_MOTOR_COL + i).background() for i in range(3)]
    check(all(b.style() == C_NONE.style() for b in home_cols), "Home row no longer green")

    # out-of-limits value -> warning, other motors still move
    tw.model.presets[2].values[0] = 500.0
    tw._rebuild()
    tw.table.selectRow(3)
    tw._on_move()
    spin(app, 0.3)
    check(len(warned) == 1 and "soft limits" in warned[0][2], "warning popup for skipped motor")

    # rename a column
    table_widget.QInputDialog.getText = staticmethod(lambda *a, **k: ("Sample X", True))
    tw._rename_motor(FIRST_MOTOR_COL)
    hdr = tw.table.horizontalHeaderItem(FIRST_MOTOR_COL).text()
    check("Sample X" in hdr and "SIM:MMS:01" in hdr, f"header shows alias and PV: {hdr!r}")

    # autosave then reload
    spin(app, 1.0)
    check(os.path.exists(path), "presets.json autosaved")
    store2 = PresetStore(path)
    store2.load()
    check(
        store2.tables[0].motors == ["SIM:MMS:01", "SIM:MMS:02", "SIM:MMS:03"]
        and [p.name for p in store2.tables[0].presets] == ["Home", "PosA", "Pasted"]
        and store2.tables[0].aliases == ["Sample X", None, None],
        "reloaded file matches (incl. alias)",
    )

    # remove motor column
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    tw._remove_motor(FIRST_MOTOR_COL + 1)
    spin(app, 0.2)
    check(
        len(tw.motors) == 2 and all(len(p.values) == 2 for p in tw.model.presets) and tw.model.aliases == ["Sample X", None],
        "remove motor column keeps aliases aligned",
    )

    # delete preset
    tw.table.clearSelection()
    tw.table.selectRow(1)
    tw._on_delete_preset()
    spin(app, 0.2)
    check([p.name for p in tw.model.presets] == ["PosA", "Pasted"], "delete preset row")

    # dock layout: new table, float it, tile, tab, layout persistence
    main_window.QInputDialog.getText = staticmethod(lambda *a, **k: ("Diode", True))
    win._new_table()
    spin(app, 0.2)
    check(len(win.docks) == 2 and win.docks[1].model.name == "Diode", "new table creates a dock")
    win.docks[1].setFloating(True)
    spin(app, 0.2)
    check(win.docks[1].isFloating() and win.docks[1].view.motors == [], "dock can float")
    win.docks[1].close()
    spin(app, 0.2)
    check(not win.docks[1].isFloating() and win.docks[1].isVisible(), "closing a floating dock re-docks it")
    win.docks[1].setFloating(True)
    spin(app, 0.1)
    win._tile_all()
    spin(app, 0.2)
    check(all(not d.isFloating() and d.isVisible() for d in win.docks), "tile all docks everything")
    check(len(win.tabifiedDockWidgets(win.docks[0])) == 0, "tiled docks are not tabbed")
    win._tab_all()
    spin(app, 0.2)
    check(win.docks[1] in win.tabifiedDockWidgets(win.docks[0]), "stack as tabs re-tabs docks")
    win.docks[1].close()
    spin(app, 0.1)
    check(not win.docks[1].isVisible(), "closing a dock hides it")
    win._fill_view_menu()
    acts = [a for a in win.view_menu.actions() if a.isCheckable()]
    check([a.text() for a in acts] == ["Table 1", "Diode"] and [a.isChecked() for a in acts] == [True, False],
          "Tables menu lists docks with visibility")
    acts[1].setChecked(True)
    spin(app, 0.1)
    check(win.docks[1].isVisible(), "Tables menu re-shows a hidden dock")
    uid = win.docks[1].model.uid
    win._set_active(win.docks[1])
    win._delete_table()
    spin(app, 0.1)
    check(len(win.docks) == 1 and all(t.uid != uid for t in store.tables), "delete table removes dock and model")

    win.close()
    spin(app, 0.3)
    store3 = PresetStore(path)
    store3.load()
    check(store3.tables[0].uid == store.tables[0].uid, "table id persisted")
    print(f"\n{failures} failure(s)")
    QTimer.singleShot(0, app.quit)
    app.exec_()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
