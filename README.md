# MotorMemory

Muscle memory for motors: a small PyQt5 tool to save and restore named motor positions at the beamline.

* **Columns** are EPICS motor records (base PV, e.g. `MFX:DG1:MMS:01`).
  The tool reads `.RBV .VAL .DMOV .EGU .DESC .LLM .HLM .RDBD .PREC`.
* **Rows** are named presets, one coordinate per motor.
* A pinned **LIVE** row shows the current readback (green = connected,
  yellow = moving, red = disconnected).
* Several independent tables live in tabs (e.g. "Sample stage", "Diode").

Everything autosaves to a JSON file (default `~/.motormemory/presets.json`,
override with `--file` or `$MOTORMEMORY_FILE`, e.g. to share a file in the
experiment directory).

## Workflow

1. Type or paste one or more motor base PVs in the top bar and press
   **Add motor(s)** (or **Paste PV names** to take them from the clipboard).
   Trailing `.RBV`/`.VAL` are stripped. The header shows `●` green when
   connected and the description/units once they arrive.
2. Create preset rows:
   * **Capture current** - snapshot the live readbacks under a name.
   * **Paste values** / Ctrl+V - paste rows of numbers (tab, comma or space
     separated; a leading non-numeric cell becomes the preset name). If a
     motor cell is selected the paste starts there, otherwise rows are added.
   * **New empty row** then type into cells.
   * **Update selected from live** - overwrite a row with the readbacks.
3. Select a row and press **Restore selected**. A confirmation dialog lists
   motor / description / current / target / delta and which motors will be
   skipped (empty value, disconnected, no write access, outside soft limits).
   On **Move** all `.VAL` fields are written at once; the row's cells turn
   yellow while `DMOV=0` and green once `|RBV - target| <= tolerance`.
   Tolerance is the motor's `RDBD` (fallback 0.01) unless the per-table
   **Tolerance** box is non-zero.

Right-click a column header to move/remove a motor; right-click a row to
duplicate/move/delete it or copy the table as text. Double-click a LIVE cell to
copy its value. Delete key removes selected rows.

## Running

### Beamline (PCDS conda, pyca/psp backend - same env as camviewer)

    ./run_motormemory.sh [--file /path/to/presets.json]

### S3DF development (ana conda, pyepics backend) with a fake IOC

    ./run_dev.sh --ioc --count 3      # terminal 1: SIM:MMS:01..03
    ./run_dev.sh                      # terminal 2: GUI
    QT_QPA_PLATFORM=offscreen ./run_dev.sh ; python test_gui.py   # headless smoke test

`pv_backend.py` picks pyca/psp if importable, otherwise pyepics. The CA
backend in use is shown in the status bar at startup.

## Files

| File | Purpose |
|---|---|
| `motormemory.py` | entry point / argument parsing |
| `main_window.py` | tabs, toolbar, autosave |
| `table_widget.py` | one table: motor columns, LIVE row, presets, paste, restore dialog |
| `motor.py` | `Motor` QObject: motor-record fields -> Qt signals |
| `pv_backend.py` | pyca/psp or pyepics wrapper with a common API |
| `presets_model.py` | data model + atomic JSON persistence + paste parsers |
| `sim_ioc.py` | caproto soft IOC with fake motors for testing |
| `test_gui.py` | headless end-to-end test against the sim IOC |

## File format

```json
{"version": 1, "tables": [
  {"name": "Sample stage",
   "motors": ["MFX:DG1:MMS:01", "MFX:DG1:MMS:02"],
   "tolerance": null,
   "presets": [{"name": "Pos A", "values": [1.0, null], "note": "", "saved": "2026-11-02 10:12:00"}]}]}
```

`null` means "no value for this motor"; it is skipped on restore.
