"""
Pure-python data model for motor preset tables, plus atomic JSON persistence.

File layout (version 1)::

    {"version": 1,
     "tables": [
        {"name": "Sample stage",
         "motors": ["MFX:DG1:MMS:01", "MFX:DG1:MMS:02"],
         "aliases": ["Sample X", null],
         "tolerance": null,
         "presets": [
            {"name": "Pos A", "values": [1.0, 2.5], "note": "", "saved": "..."}
         ]}
     ]}

``null`` in ``values`` means "no value for this motor" and is skipped on
restore.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import math
import os
import shutil
import tempfile
import typing
import uuid

DEFAULT_FILE = os.path.expanduser("~/.motormemory/presets.json")
Number = typing.Optional[float]


def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Preset:
    def __init__(
        self,
        name: str,
        values: typing.Iterable[Number],
        note: str = "",
        saved: str = "",
    ):
        self.name = name
        self.values: list[Number] = [_num(v) for v in values]
        self.note = note
        self.saved = saved or now_str()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "values": self.values,
            "note": self.note,
            "saved": self.saved,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Preset":
        return cls(
            str(d.get("name", "")),
            d.get("values", []),
            str(d.get("note", "")),
            str(d.get("saved", "")),
        )


class Table:
    def __init__(
        self,
        name: str,
        motors: typing.Optional[list[str]] = None,
        presets: typing.Optional[list[Preset]] = None,
        tolerance: Number = None,
        aliases: typing.Optional[list[typing.Optional[str]]] = None,
        uid: typing.Optional[str] = None,
    ):
        self.name = name
        # Stable id used to remember window layout across renames
        self.uid: str = uid or uuid.uuid4().hex[:12]
        self.motors: list[str] = list(motors or [])
        self.presets: list[Preset] = list(presets or [])
        self.tolerance: Number = tolerance  # None => use each motor's RDBD
        # Friendly display names, parallel to ``motors`` (None => show the PV)
        self.aliases: list[typing.Optional[str]] = list(aliases or [])
        self._normalize()

    def _normalize(self) -> None:
        n = len(self.motors)
        self.aliases = [(a or None) for a in self.aliases][:n]
        self.aliases.extend([None] * (n - len(self.aliases)))
        for p in self.presets:
            if len(p.values) < n:
                p.values.extend([None] * (n - len(p.values)))
            elif len(p.values) > n:
                del p.values[n:]

    # ---------------------------------------------------------- motors
    def add_motor(self, base: str) -> int:
        base = base.strip()
        if base in self.motors:
            return self.motors.index(base)
        self.motors.append(base)
        self.aliases.append(None)
        for p in self.presets:
            p.values.append(None)
        return len(self.motors) - 1

    def remove_motor(self, index: int) -> None:
        del self.motors[index]
        del self.aliases[index]
        for p in self.presets:
            del p.values[index]

    def move_motor(self, src: int, dst: int) -> None:
        if src == dst:
            return
        self.motors.insert(dst, self.motors.pop(src))
        self.aliases.insert(dst, self.aliases.pop(src))
        for p in self.presets:
            p.values.insert(dst, p.values.pop(src))

    def set_alias(self, index: int, alias: typing.Optional[str]) -> None:
        alias = (alias or "").strip()
        self.aliases[index] = alias or None

    def label(self, index: int) -> str:
        """Friendly name if set, else the PV base name."""
        return self.aliases[index] or self.motors[index]

    # --------------------------------------------------------- presets
    def add_preset(self, name: str, values: typing.Iterable[Number], note="") -> Preset:
        vals = list(values)
        vals = (vals + [None] * len(self.motors))[: len(self.motors)]
        p = Preset(name, vals, note)
        self.presets.append(p)
        return p

    def remove_preset(self, index: int) -> None:
        del self.presets[index]

    def move_preset(self, src: int, dst: int) -> None:
        if src == dst:
            return
        self.presets.insert(dst, self.presets.pop(src))

    def unique_preset_name(self, stem: str = "Preset") -> str:
        names = {p.name for p in self.presets}
        if stem not in names:
            return stem
        i = 2
        while f"{stem} {i}" in names:
            i += 1
        return f"{stem} {i}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "id": self.uid,
            "motors": self.motors,
            "aliases": self.aliases,
            "tolerance": self.tolerance,
            "presets": [p.to_dict() for p in self.presets],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Table":
        return cls(
            str(d.get("name", "Table")),
            [str(m) for m in d.get("motors", [])],
            [Preset.from_dict(p) for p in d.get("presets", [])],
            _num(d.get("tolerance")),
            [(None if a in (None, "") else str(a)) for a in d.get("aliases", [])],
            str(d["id"]) if d.get("id") else None,
        )


class PresetStore:
    VERSION = 1

    def __init__(self, path: str = DEFAULT_FILE):
        self.path = path
        self.tables: list[Table] = []

    # ------------------------------------------------------------ I/O
    def load(self) -> None:
        self.tables = []
        if not os.path.exists(self.path):
            return
        with open(self.path) as fd:
            data = json.load(fd)
        for t in data.get("tables", []):
            self.tables.append(Table.from_dict(t))

    def save(self) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        with atomic_writer(self.path) as fd:
            json.dump(
                {"version": self.VERSION, "tables": [t.to_dict() for t in self.tables]},
                fd,
                indent=2,
            )
            fd.write("\n")

    # --------------------------------------------------------- tables
    def unique_table_name(self, stem: str = "Table") -> str:
        names = {t.name for t in self.tables}
        if stem not in names:
            return stem
        i = 2
        while f"{stem} {i}" in names:
            i += 1
        return f"{stem} {i}"

    def add_table(self, name: typing.Optional[str] = None) -> Table:
        t = Table(name or self.unique_table_name())
        self.tables.append(t)
        return t

    def remove_table(self, table: Table) -> None:
        self.tables.remove(table)


# ------------------------------------------------------------------ utils
def _num(v) -> Number:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


@contextlib.contextmanager
def atomic_writer(path: str) -> typing.Iterator[typing.TextIO]:
    """Write to a temp file then move into place (from camviewer)."""
    d = os.path.dirname(os.path.abspath(path))
    with tempfile.NamedTemporaryFile("w", delete=False, dir=d) as fd:
        try:
            yield fd
        except Exception as exc:
            print(f"Error writing {path}: {exc}")
        else:
            fd.close()
            os.chmod(fd.name, 0o644)
            shutil.move(fd.name, path)
    if os.path.exists(fd.name):
        os.remove(fd.name)


def parse_pasted_values(text: str) -> list[list[typing.Optional[str]]]:
    """
    Parse clipboard text into rows of cells.

    Accepts tab-, comma-, semicolon- or whitespace-separated numbers.  Empty
    cells become None.  Lines starting with '#' are ignored.
    """
    rows: list[list[typing.Optional[str]]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            cells = line.split("\t")
        elif "," in line:
            cells = line.split(",")
        elif ";" in line:
            cells = line.split(";")
        else:
            cells = line.split()
        rows.append([c.strip() or None for c in cells])
    return rows


def parse_pv_names(text: str) -> list[str]:
    """Split pasted text into PV base names (strips motor-record fields)."""
    names: list[str] = []
    for tok in text.replace(",", " ").replace(";", " ").split():
        tok = tok.strip()
        if not tok or tok.startswith("#"):
            continue
        base = tok
        if "." in tok:
            head, field = tok.rsplit(".", 1)
            if field.upper() in {"RBV", "VAL", "DMOV", "EGU", "DESC", "MOVN", "LLM", "HLM"}:
                base = head
        if base not in names:
            names.append(base)
    return names
