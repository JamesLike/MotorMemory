"""
Post MotorMemory events to the LCLS eLog.

Uses the PCDS ``elog`` package (``elog.HutchELog``) that ships in the PCDS
conda environment.  Authentication is Kerberos: posts appear as whoever's
ticket is active on the machine (e.g. mfxopr on the control machine).

All network traffic happens on a background thread; results are delivered
through Qt signals so the GUI never blocks on the eLog server.
"""

from __future__ import annotations

import datetime
import html
import queue
import threading
import typing

from PyQt5.QtCore import QObject, pyqtSignal

TAGS = ["motormemory"]


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


class ElogPoster(QObject):
    """
    Lazily-connected eLog client with a worker thread.

    ``available`` / ``unavailable_reason`` tell the GUI whether to enable the
    checkbox.  ``post(title, html_body, tags)`` queues a post; ``posted`` or
    ``failed`` is emitted when it completes.
    """

    posted = pyqtSignal(str)  # title
    failed = pyqtSignal(str, str)  # title, error
    connected = pyqtSignal(str)  # description of the logbook

    def __init__(self, hutch: typing.Optional[str] = None, parent: typing.Optional[QObject] = None):
        super().__init__(parent)
        self.hutch = (hutch or "").upper() or None
        self.enabled = False
        self._client = None
        self._client_factory = None  # overridable for tests
        self._queue: "queue.Queue[typing.Optional[tuple[str, str, list[str]]]]" = queue.Queue()
        self._thread: typing.Optional[threading.Thread] = None
        self.available, self.unavailable_reason = self._probe()

    # ------------------------------------------------------------ setup
    @staticmethod
    def _probe() -> tuple[bool, str]:
        try:
            import importlib

            importlib.import_module("elog")
        except Exception as exc:
            return False, f"PCDS 'elog' package not importable ({exc})"
        return True, ""

    def set_hutch_from_pv(self, pv: str) -> None:
        """Fallback: derive the instrument from a PV prefix (MFX:DG1:MMS:01 -> MFX)."""
        if self.hutch or not pv or ":" not in pv:
            return
        head = pv.split(":")[0].upper()
        if 2 <= len(head) <= 4 and head.isalpha():
            self.hutch = head

    def _make_client(self):
        if self._client_factory is not None:
            return self._client_factory(self.hutch)
        from elog import HutchELog

        return HutchELog(self.hutch)

    # ------------------------------------------------------------ posting
    def post(self, title: str, body_html: str, tags: typing.Optional[list[str]] = None) -> bool:
        """Queue a post.  Returns False if posting is disabled/unavailable."""
        if not (self.enabled and self.available):
            return False
        if not self.hutch:
            self.failed.emit(title, "hutch unknown: pass --hutch or add a motor with a HUTCH: prefix")
            return False
        self._queue.put((title, body_html, TAGS + [t for t in (tags or []) if t]))
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, name="elog-poster", daemon=True)
            self._thread.start()
        return True

    def _worker(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=30)
            except queue.Empty:
                return
            if item is None:
                return
            title, body, tags = item
            try:
                if self._client is None:
                    self._client = self._make_client()
                    books = getattr(self._client, "logbooks", {}) or {}
                    exp = books.get("experiment")
                    self.connected.emit(f"{self.hutch} eLog" + (f" ({exp})" if exp else ""))
                self._client.post(body, tags=tags, title=title, experiment=True, facility=False)
                self.posted.emit(title)
            except Exception as exc:  # network / auth / anything
                self._client = None  # force a fresh login next time
                self.failed.emit(title, str(exc))

    def shutdown(self) -> None:
        self._queue.put(None)


# ====================================================================== formatting
def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _table(headers: list[str], rows: list[list[typing.Any]]) -> str:
    out = ["<table border='1' cellpadding='3' cellspacing='0'>", "<tr>"]
    out += [f"<th>{_esc(h)}</th>" for h in headers]
    out.append("</tr>")
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>")
    out.append("</table>")
    return "".join(out)


def format_move(
    table: str,
    preset: str,
    rows: list[dict],
    skipped: list[str],
    settled: bool,
) -> tuple[str, str]:
    """
    rows: dicts with keys label, pv, frm, target, final, ok (bool|None).
    Returns (title, html).
    """
    title = f"MotorMemory: {table} \u2192 '{preset}'"
    body = [f"<p><b>{_esc(title)}</b> &mdash; {_stamp()}</p>"]
    trows = []
    for r in rows:
        status = "at target" if r.get("ok") else ("not settled" if r.get("ok") is None else "OFF target")
        trows.append([r["label"], r["pv"], r["frm"], r["target"], r["final"], status])
    body.append(_table(["Motor", "PV", "From", "Target", "Final", ""], trows))
    if skipped:
        body.append("<p>Skipped:<br>" + "<br>".join(_esc(s) for s in skipped) + "</p>")
    if not settled:
        body.append("<p><i>Posted before all motors finished moving (timeout).</i></p>")
    return title, "".join(body)


def format_changes(table: str, summary: str, changes: list[list[typing.Any]], headers: list[str]) -> tuple[str, str]:
    """Generic 'something changed' post with a table of changes."""
    title = f"MotorMemory: {table}: {summary}"
    body = [f"<p><b>{_esc(title)}</b> &mdash; {_stamp()}</p>"]
    if changes:
        body.append(_table(headers, changes))
    return title, "".join(body)
