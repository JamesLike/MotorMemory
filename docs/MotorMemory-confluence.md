# MotorMemory – save and restore named motor positions

MotorMemory is a small table-based GUI for the beamline. Each **column** is an EPICS motor, each **row** is a named position (preset). You can snapshot where the motors are now, paste or type positions, and move every motor back to a saved row with one click. Cells turn green when the motor is at that position, so the table also shows you which preset you are currently at.

Source code: https://github.com/JamesLike/MotorMemory

Typical uses: sample/holder positions on an x,y,z stage, alignment-screen in/out positions, detector stand positions, "beam-on-YAG" vs "beam-on-sample" configurations.

> **Note:** MotorMemory moves motors immediately when you press *Move to selected*; there is no confirmation dialog. Soft limits are respected (out-of-limit values are skipped and reported), but it does not check for collisions. Use the same care as with any motor move.

---

## Starting MotorMemory

MotorMemory needs Channel Access to the motor IOCs, so run it on a machine on the controls network (e.g. `mfx-control`, or wherever you run camviewer). It uses the same PCDS conda environment as camviewer.

```
~jb2717/MotorMemory/run_motormemory.sh
```

Options:

| Option | Meaning |
|---|---|
| `--file /path/to/presets.json` | Use a specific presets file (e.g. one shared by the experiment in its folder). Default: `~/.motormemory/presets.json` |
| `MOTORMEMORY_FILE=/path/presets.json` (env var) | Same as `--file` |
| `--style Fusion` | Qt widget style (default `Windows`, as camviewer) |

The status bar at the bottom shows which EPICS library is in use (`CA backend: pyca` on the beamline). The window title shows the presets file path.

To install your own copy:

```
git clone git@github.com:JamesLike/MotorMemory.git
./MotorMemory/run_motormemory.sh
```

---

## The window

![screenshot placeholder: main window with two tables in tabs]

- **Tabs** – one table per group of motors (e.g. "Sample stage", "Diode"). *New table*, *Rename table*, *Delete table* are in the toolbar; double-click a tab to rename it; drag tabs to reorder.
- **Motor PV bar** – where you add motors (columns).
- **Table** – row 0 is the pinned **LIVE** row (current readback of every motor). Rows below are presets.
- **Buttons** – *Capture current*, *Update selected from live*, *Paste values*, *New empty row*, *Delete row(s)*, and **Move to selected**.
- **Tolerance** – per-table settle tolerance (see *Colours*).

Everything **autosaves** to the presets file about half a second after each edit. *Save now* (Ctrl+S) and *Reload from disk* are in the toolbar.

---

## Step 1 – Add motors (columns)

1. Type one or more motor **base PV names** in the *Motor PV* box, e.g. `MFX:DG1:MMS:01 MFX:DG1:MMS:02`, and press Enter or *Add motor(s)*.
   - Several PVs may be separated by spaces, commas or newlines.
   - A trailing field such as `.RBV` or `.VAL` is stripped automatically, so you can paste PV names straight from camviewer or an EDM screen.
   - Or copy a list of PVs and press *Paste PV names*.
2. The column header shows **● green** once the motor connects, with its units and description (`EGU`, `DESC`). **○ red** means not connected (wrong name, IOC down, or not on the controls network).
3. The **LIVE** cell shows the current readback and is yellow while the motor is moving.

MotorMemory reads these motor-record fields: `.RBV .VAL .DMOV .EGU .DESC .LLM .HLM .RDBD .PREC`. Any EPICS motor record works (IMS, Newport, SmarAct, PI, …).

### Friendly column names

PV names are long. **Double-click a column header** (or right-click → *Rename column…*) and give it a short name such as `Sample X`. The header then shows the friendly name on the first line and the PV on the second. Leave the name empty to go back to showing the PV. Friendly names are saved with the table.

### Column menu (right-click a header)

*Rename column…*, *Copy PV name*, *Reconnect*, *Move left / right*, *Remove motor column* (removes that motor's values from every preset).

---

## Step 2 – Create presets (rows)

Any of these:

| Action | What it does |
|---|---|
| **Capture current** | Asks for a name and adds a row with the live readback of every connected motor. The most common way to save a position. |
| **Update selected from live** | Overwrites the selected row(s) with the current readbacks (keeps the name). Use after fine-tuning a position. |
| **Paste values** / Ctrl+V | Pastes rows of numbers from the clipboard. Separators can be tab, comma, semicolon or spaces, so it works from Excel, the eLog, or a text file. If the first cell of a line is not a number it becomes the preset name. If a motor cell is selected, pasting starts at that cell; otherwise new rows are appended. |
| **New empty row** | Adds a blank row; type values into the cells. |
| Type in a cell | Edit any value directly. Non-numeric text is rejected. Empty cell = "no value for this motor" (that motor is left alone when moving to the row). |
| Edit the **Preset** column | Renames the row. The **Note** column is free text. |

Example clipboard text that pastes as two named rows:

```
Sample A	1.250	-0.400	12.00
Sample B	1.300	-0.400	12.00
```

### Row menu (right-click a row)

*Move to this preset*, *Update from live*, *Duplicate row*, *Move up / down*, *Delete row(s)*, *Paste values here*, *Copy table as text* (tab-separated, handy for pasting into the eLog).

Delete/Backspace removes the selected rows (after a confirmation).

---

## Step 3 – Move to a preset

1. Click a row to select it.
2. Press **Move to selected ▶** (or right-click → *Move to this preset*).

All motors in that row are commanded at once by writing their `.VAL` fields. There is no confirmation dialog. Only the button and the menu entry start a move; double-clicking or pressing Enter on a row does not.

A motor is **skipped** (the others still move) if:

- the cell is empty,
- the motor is disconnected,
- you do not have write access to it,
- the value is outside the motor's soft limits (`LLM`/`HLM`), or
- the put fails.

If anything was skipped, a popup lists the motors and the reason after the move is started.

---

## Colours

Cell colours are live and apply to **every row**, not just the one you last moved to:

| Colour | Meaning |
|---|---|
| **Green** | The motor's readback is at this cell's value (within tolerance). A fully green row means "you are at this preset". |
| **Yellow** | The motor is moving toward this cell's value. |
| Grey | The motor is disconnected. |
| **Red** | The value is outside the motor's soft limits (will be skipped). |
| White | Not at this value. |

**Tolerance**: by default each motor's own `RDBD` (retry deadband) field, falling back to 0.01 if it is zero. Set the *Tolerance* box to a non-zero value to use one tolerance for the whole table instead (e.g. `0.005` mm). This affects colouring only, never the move itself.

In the LIVE row, green = connected and stopped, yellow = moving, red = disconnected. Double-click a LIVE cell to copy its value.

---

## Sharing presets with the experiment

Presets live in one JSON file. To share with the whole shift, point everyone at a file in the experiment directory:

```
~jb2717/MotorMemory/run_motormemory.sh --file /cds/home/opr/mfxopr/mfxlx1234/motor_presets.json
```

Writes are atomic (temp file + rename), so two people editing simultaneously will not corrupt the file, but the last save wins – use *Reload from disk* to pick up someone else's changes.

The file is plain JSON and can be edited or version-controlled:

```json
{"version": 1, "tables": [
  {"name": "Sample stage",
   "motors": ["MFX:DG1:MMS:01", "MFX:DG1:MMS:02"],
   "aliases": ["Sample X", null],
   "tolerance": null,
   "presets": [
     {"name": "Sample A", "values": [1.25, -0.4], "note": "", "saved": "2026-11-02 10:12:00"}
   ]}]}
```

`null` in `values` = no value for that motor; `null` in `aliases` = show the PV name; `"tolerance": null` = use RDBD.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Header stays **○ red**, LIVE cell says *disconnected* | Wrong PV name (check with `caget PV.RBV`), IOC down, or you are not on the controls network (psdev/S3DF cannot reach IOCs). Right-click header → *Reconnect* after fixing. |
| Status bar says `CA backend: pyepics` or `NONE` | The PCDS conda env did not load; check that `run_motormemory.sh` could `source /cds/group/pcds/pyps/conda/pcds_conda`. pyepics works too, but pyca is the tested path at the beamline. |
| *Move to selected* does nothing | Exactly one preset row must be selected (not the LIVE row). Check the status bar message. |
| Popup "Some motors were not moved" | Read the reasons listed: disconnected, no write access, outside soft limits, put failed. |
| Cell never turns green after a move | Motor stopped short of the target by more than the tolerance (check `RDBD`, backlash, or set a per-table tolerance). The LIVE row shows where it actually is. |
| Values show with the wrong number of decimals | Display precision follows the motor's `PREC` field. The stored value is not rounded. |
| Lost edits | Everything autosaves within ~0.5 s; check the window title for which file is in use. |

Errors are also printed to the terminal MotorMemory was started from.

---

## Development and testing (S3DF)

The code has a pyca/psp backend (beamline) and a pyepics fallback, so it can be developed on S3DF against a fake IOC:

```
cd MotorMemory
./run_dev.sh --ioc --count 3     # terminal 1: caproto soft IOC, SIM:MMS:01..03
./run_dev.sh                     # terminal 2: GUI
QT_QPA_PLATFORM=offscreen ./run_dev.sh ; python test_gui.py   # headless end-to-end test
```

| File | Purpose |
|---|---|
| `motormemory.py` | entry point / argument parsing |
| `main_window.py` | tabs, toolbar, autosave |
| `table_widget.py` | one table: motor columns, LIVE row, presets, paste, move |
| `motor.py` | `Motor` QObject: motor-record fields → Qt signals |
| `pv_backend.py` | pyca/psp or pyepics wrapper with a common API |
| `presets_model.py` | data model, atomic JSON persistence, paste parsers |
| `sim_ioc.py`, `test_gui.py` | fake motors and automated test |

Bug reports and feature requests: open an issue at https://github.com/JamesLike/MotorMemory/issues or contact James Baxter (jb2717).
