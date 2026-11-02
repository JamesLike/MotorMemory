"""
Motor: a QObject wrapping the fields of one EPICS motor record.

All CA callbacks arrive on the CA library thread; this class converts them
to Qt signals so the GUI only ever touches widgets from the GUI thread
(the same pattern camviewer uses: sizeCallback -> sizeUpdate.emit()).
"""

from __future__ import annotations

import math
import typing

from PyQt5.QtCore import QObject, pyqtSignal

from pv_backend import PV

# Fields read from the motor record.  Base PV + "." + field.
FIELDS = ("RBV", "VAL", "DMOV", "EGU", "DESC", "LLM", "HLM", "RDBD", "PREC")


class Motor(QObject):
    """One motor record (e.g. MFX:DG1:MMS:01)."""

    connectionChanged = pyqtSignal(bool)  # RBV connection state
    readbackChanged = pyqtSignal(float)
    movingChanged = pyqtSignal(bool)
    infoChanged = pyqtSignal()  # DESC / EGU / limits / precision updated

    DEFAULT_TOLERANCE = 0.01

    def __init__(self, base: str, parent: typing.Optional[QObject] = None):
        super().__init__(parent)
        self.base = base.strip()
        self.readback: typing.Optional[float] = None
        self.setpoint: typing.Optional[float] = None
        self.moving: bool = False
        self.desc: str = ""
        self.egu: str = ""
        self.llm: typing.Optional[float] = None
        self.hlm: typing.Optional[float] = None
        self.rdbd: typing.Optional[float] = None
        self.prec: int = 3
        self.connected: bool = False

        self._pvs: dict[str, PV] = {}
        for f in FIELDS:
            pv = PV(f"{self.base}.{f}")
            self._pvs[f] = pv
        self._pvs["RBV"].add_connection_callback(self._on_rbv_conn)
        self._pvs["RBV"].add_value_callback(self._on_rbv)
        self._pvs["VAL"].add_value_callback(self._on_val)
        self._pvs["DMOV"].add_value_callback(self._on_dmov)
        for f in ("EGU", "DESC", "LLM", "HLM", "RDBD", "PREC"):
            self._pvs[f].add_value_callback(self._make_info_cb(f))

    # ------------------------------------------------------------------ CA
    def connect(self) -> None:
        for pv in self._pvs.values():
            pv.connect()

    def disconnect(self) -> None:
        for pv in self._pvs.values():
            pv.disconnect()

    def _on_rbv_conn(self, connected: bool) -> None:
        self.connected = connected
        self.connectionChanged.emit(connected)

    def _on_rbv(self, value) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        self.readback = v
        self.readbackChanged.emit(v)

    def _on_val(self, value) -> None:
        try:
            self.setpoint = float(value)
        except (TypeError, ValueError):
            pass

    def _on_dmov(self, value) -> None:
        try:
            moving = int(value) == 0
        except (TypeError, ValueError):
            return
        self.moving = moving
        self.movingChanged.emit(moving)

    def _make_info_cb(self, field: str):
        def cb(value):
            try:
                if field == "EGU":
                    self.egu = _to_str(value)
                elif field == "DESC":
                    self.desc = _to_str(value)
                elif field == "LLM":
                    self.llm = float(value)
                elif field == "HLM":
                    self.hlm = float(value)
                elif field == "RDBD":
                    self.rdbd = float(value)
                elif field == "PREC":
                    self.prec = max(0, int(value))
            except (TypeError, ValueError):
                return
            self.infoChanged.emit()

        return cb

    # ------------------------------------------------------------ actions
    @property
    def write_access(self) -> bool:
        return self._pvs["VAL"].write_access

    def move(self, target: float) -> bool:
        """Issue an asynchronous put to .VAL.  Returns False on failure."""
        return self._pvs["VAL"].put(float(target))

    def read_fresh(self, timeout: float = 1.0) -> typing.Optional[float]:
        """Synchronous read of .RBV (used by 'Capture current')."""
        v = self._pvs["RBV"].get(timeout=timeout)
        if v is None:
            return self.readback
        try:
            self.readback = float(v)
        except (TypeError, ValueError):
            return self.readback
        return self.readback

    # ------------------------------------------------------------ helpers
    @property
    def tolerance(self) -> float:
        if self.rdbd is not None and self.rdbd > 0:
            return self.rdbd
        return self.DEFAULT_TOLERANCE

    def in_limits(self, target: float) -> bool:
        """True if target is within LLM..HLM (limits equal => disabled)."""
        if self.llm is None or self.hlm is None or self.llm == self.hlm:
            return True
        lo, hi = sorted((self.llm, self.hlm))
        return lo <= target <= hi

    def at_target(self, target: float, tol: typing.Optional[float] = None) -> bool:
        if self.readback is None or target is None or math.isnan(target):
            return False
        t = self.tolerance if tol is None else max(tol, 0.0)
        return abs(self.readback - target) <= t

    def fmt(self, value: typing.Optional[float]) -> str:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return ""
        return f"{value:.{self.prec}f}"

    def tooltip(self) -> str:
        lines = [self.base]
        if self.desc:
            lines.append(self.desc)
        if self.egu:
            lines.append(f"units: {self.egu}")
        if self.llm is not None and self.hlm is not None:
            lines.append(f"limits: {self.fmt(self.llm)} .. {self.fmt(self.hlm)}")
        lines.append(f"tolerance: {self.tolerance:g}")
        lines.append("connected" if self.connected else "DISCONNECTED")
        return "\n".join(lines)


def _to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("ascii", errors="ignore").rstrip("\x00")
    if isinstance(value, str):
        return value
    # char waveform as sequence of ints (camviewer's decode_char_waveform)
    try:
        chars = []
        for c in value:
            c = int(c)
            if c == 0:
                break
            chars.append(chr(c & 0xFF))
        return "".join(chars)
    except TypeError:
        return str(value)
