"""
Thin EPICS Channel Access wrapper with two backends.

Preferred backend is pyca / psp.Pv (the PCDS conda environment that
camviewer runs in on the beamline machines).  If that is not importable we
fall back to pyepics, which is available in the S3DF ana/ps conda envs and
lets the GUI be developed and tested against a soft IOC.

Both backends expose the same tiny API through :class:`PV`:

    pv = PV("MFX:DG1:MMS:01.RBV")
    pv.add_connection_callback(fn)   # fn(connected: bool)
    pv.add_value_callback(fn)        # fn(value)
    pv.connect()                     # non-blocking; monitors start on connect
    pv.connected, pv.value, pv.write_access
    pv.get(timeout=1.0)              # synchronous fresh read (or None)
    pv.put(value)                    # asynchronous write
    pv.disconnect()

Callbacks are invoked from the CA library thread.  Callers must marshal
them onto the Qt GUI thread (e.g. by emitting a pyqtSignal), exactly as
camviewer does.
"""

from __future__ import annotations

import threading
import typing

BACKEND = None

try:  # pragma: no cover - depends on the environment
    import pyca  # noqa: F401
    from psp.Pv import Pv as _PspPv

    BACKEND = "pyca"
except Exception:  # pragma: no cover
    try:
        import epics as _epics

        BACKEND = "pyepics"
    except Exception:
        BACKEND = None


ConnCB = typing.Callable[[bool], None]
ValueCB = typing.Callable[[typing.Any], None]


class _PVBase:
    def __init__(self, name: str):
        self.name = name
        self._conn_cbs: list[ConnCB] = []
        self._value_cbs: list[ValueCB] = []
        self._lock = threading.Lock()

    def add_connection_callback(self, cb: ConnCB) -> None:
        with self._lock:
            self._conn_cbs.append(cb)

    def add_value_callback(self, cb: ValueCB) -> None:
        with self._lock:
            self._value_cbs.append(cb)

    def _fire_conn(self, connected: bool) -> None:
        with self._lock:
            cbs = list(self._conn_cbs)
        for cb in cbs:
            try:
                cb(connected)
            except Exception as exc:  # never let a callback kill the CA thread
                print(f"{self.name}: connection callback error: {exc}")

    def _fire_value(self, value) -> None:
        with self._lock:
            cbs = list(self._value_cbs)
        for cb in cbs:
            try:
                cb(value)
            except Exception as exc:
                print(f"{self.name}: value callback error: {exc}")


if BACKEND == "pyca":

    class PV(_PVBase):
        """pyca / psp.Pv implementation (beamline machines)."""

        def __init__(self, name: str):
            super().__init__(name)
            self._pv = _PspPv(name, use_numpy=False)
            self._pv.add_connection_callback(self._on_conn)
            self._pv.add_monitor_callback(self._on_mon)
            self._pv.add_rwaccess_callback(self._on_rw)
            self._monitoring = False
            self._write_access: typing.Optional[bool] = None  # None = unknown

        # -- camviewer's monitor_on_connect pattern ------------------------
        def _on_conn(self, is_connected: bool) -> None:
            if is_connected and not self._monitoring:
                self._monitoring = True
                try:
                    # Subscribe only; do not issue a get() from inside the
                    # CA callback (newer psp has wait_for_init, older lacks it).
                    try:
                        self._pv.monitor(pyca.DBE_VALUE, wait_for_init=False)
                    except TypeError:
                        self._pv.monitor(pyca.DBE_VALUE)
                    pyca.flush_io()
                except Exception as exc:
                    print(f"{self.name}: monitor failed: {exc}")
            self._fire_conn(bool(is_connected))

        def _on_mon(self, exception=None) -> None:
            if exception is None:
                self._fire_value(self._pv.value)

        def _on_rw(self, read_access: bool, write_access: bool) -> None:
            self._write_access = bool(write_access)

        def connect(self) -> None:
            try:
                self._pv.connect(None)
                pyca.flush_io()
            except Exception as exc:
                print(f"{self.name}: connect failed: {exc}")

        @property
        def connected(self) -> bool:
            return bool(getattr(self._pv, "isconnected", False))

        @property
        def write_access(self) -> bool:
            # Access-rights events only arrive after psp initialises the PV;
            # until then assume writable so a restore is not silently skipped
            # (the put itself will fail loudly if we lack permission).
            if self._write_access is None:
                return True
            return self._write_access

        @property
        def value(self):
            try:
                return self._pv.value
            except Exception:
                return None

        def get(self, timeout: float = 1.0):
            try:
                if not self.connected:
                    self._pv.wait_ready(timeout)
                self._pv.get(ctrl=False, timeout=timeout)
                return self._pv.value
            except Exception as exc:
                print(f"{self.name}: get failed: {exc}")
                return None

        def put(self, value, timeout: float = 1.0) -> bool:
            try:
                self._pv.put(value, timeout)
                pyca.flush_io()
                return True
            except Exception as exc:
                print(f"{self.name}: put failed: {exc}")
                return False

        def disconnect(self) -> None:
            try:
                if self._monitoring:
                    self._pv.monitor_stop()
                self._pv.disconnect()
                pyca.flush_io()
            except Exception:
                pass

elif BACKEND == "pyepics":

    class PV(_PVBase):
        """pyepics implementation (development / S3DF)."""

        def __init__(self, name: str):
            super().__init__(name)
            self._pv: typing.Optional[_epics.PV] = None

        def _on_conn(self, pvname=None, conn=False, **kw) -> None:
            self._fire_conn(bool(conn))

        def _on_mon(self, pvname=None, value=None, **kw) -> None:
            self._fire_value(value)

        def connect(self) -> None:
            if self._pv is None:
                self._pv = _epics.PV(
                    self.name,
                    callback=self._on_mon,
                    connection_callback=self._on_conn,
                    auto_monitor=True,
                )

        @property
        def connected(self) -> bool:
            return bool(self._pv is not None and self._pv.connected)

        @property
        def write_access(self) -> bool:
            if self._pv is None or not self._pv.connected:
                return False
            return bool(self._pv.write_access)

        @property
        def value(self):
            if self._pv is None:
                return None
            return self._pv.value

        def get(self, timeout: float = 1.0):
            self.connect()
            if not self._pv.wait_for_connection(timeout=timeout):
                return None
            return self._pv.get(timeout=timeout, use_monitor=False)

        def put(self, value, timeout: float = 1.0) -> bool:
            self.connect()
            try:
                rc = self._pv.put(value, wait=False)
                return rc is not None and rc >= 0
            except Exception as exc:
                print(f"{self.name}: put failed: {exc}")
                return False

        def disconnect(self) -> None:
            if self._pv is not None:
                try:
                    self._pv.clear_callbacks()
                    self._pv.disconnect()
                except Exception:
                    pass
                self._pv = None

else:

    class PV(_PVBase):  # type: ignore[no-redef]
        """No CA library available - everything is disconnected."""

        def connect(self) -> None:
            self._fire_conn(False)

        connected = False
        write_access = False
        value = None

        def get(self, timeout: float = 1.0):
            return None

        def put(self, value, timeout: float = 1.0) -> bool:
            return False

        def disconnect(self) -> None:
            pass


def caget(name: str, timeout: float = 1.0):
    """One-shot synchronous read (returns None on failure)."""
    pv = PV(name)
    try:
        return pv.get(timeout=timeout)
    finally:
        pv.disconnect()
