"""Logger self-telemetry: heartbeat + lifecycle event log.

STRICT ISOLATION: nothing in this module may raise into main.py's sensor-read
or data-send path. Every public entry point swallows its own exceptions and
degrades to a no-op. Telemetry going dark must never take data delivery with it.
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid

SENSOR_KEYS = ("ph", "tss", "debit", "cod", "nh3n")


# ── Pure helpers ────────────────────────────────────────────────────

def clamp_pct(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, round(v, 1)))


def build_status(snap: dict) -> dict:
    """Flatten an internal snapshot into the wire `status` dict the server expects.
    Pure: no I/O. Unknown/missing sensors stay None (tri-state)."""
    sensor_ok = snap.get("sensor_ok") or {}
    status = {
        "uptime_s": snap.get("uptime_s"),
        "logger_version": snap.get("logger_version"),
        "op_status": snap.get("op_status"),
        "consec_fail": snap.get("consec_fail"),
        "internet_ok": snap.get("internet_ok"),
        "last_send_ok_mm": snap.get("last_send_ok_mm"),
        "last_send_ok_klhk": snap.get("last_send_ok_klhk"),
        "buffer_depth": snap.get("buffer_depth"),
        "daily_sent": snap.get("daily_sent"),
        "cpu_temp": snap.get("cpu_temp"),
        "cpu_pct": snap.get("cpu_pct"),
        "mem_pct": snap.get("mem_pct"),
        "disk_pct": snap.get("disk_pct"),
    }
    for k in SENSOR_KEYS:
        status[f"{k}_ok"] = sensor_ok.get(k)
    return status


def _pct_from_meminfo(text: str):
    """Parse /proc/meminfo text → used %. Returns None on garbage."""
    total = avail = None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            try:
                total = float(line.split()[1])
            except (IndexError, ValueError):
                return None
        elif line.startswith("MemAvailable:"):
            try:
                avail = float(line.split()[1])
            except (IndexError, ValueError):
                return None
    if not total or avail is None:
        return None
    return clamp_pct(100.0 * (total - avail) / total)


def _disk_pct(path: str = "/"):
    """Disk used % via os.statvfs. None on unsupported platforms (e.g. Windows dev)."""
    try:
        s = os.statvfs(path)
    except (OSError, AttributeError):
        return None
    total = s.f_blocks * s.f_frsize
    free = s.f_bfree * s.f_frsize
    if total <= 0:
        return None
    return clamp_pct(100.0 * (total - free) / total)


def read_cpu_temp():
    """Raspberry Pi CPU temp in °C from sysfs. None if unavailable."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def read_mem_pct():
    try:
        with open("/proc/meminfo") as f:
            return _pct_from_meminfo(f.read())
    except OSError:
        return None


def read_cpu_pct():
    """Best-effort load-based CPU %: 1-min loadavg / ncpu * 100. None on failure."""
    try:
        load1 = os.getloadavg()[0]
        n = os.cpu_count() or 1
        return clamp_pct(100.0 * load1 / n)
    except (OSError, AttributeError):
        return None


def read_resources() -> dict:
    """All Pi resource metrics; each independently degrades to None off-Pi."""
    return {
        "cpu_temp": read_cpu_temp(),
        "cpu_pct": read_cpu_pct(),
        "mem_pct": read_mem_pct(),
        "disk_pct": _disk_pct("/"),
    }


def build_heartbeat_payload(uid: str, status: dict) -> dict:
    return {"uid": uid, "status": status}


def build_events_payload(uid: str, events: list) -> dict:
    return {"uid": uid, "events": events}


# ── Local event store ───────────────────────────────────────────────

class EventLog:
    """Append-only local event store with a `synced` flag. Client-generates a
    unique `event_uid` per event (the server's idempotency key). All methods
    swallow sqlite errors — telemetry must never crash the app."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ok = self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=5)

    def _init_db(self) -> bool:
        try:
            with self._connect() as c:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS telemetry_events ("
                    " event_uid TEXT PRIMARY KEY, type TEXT NOT NULL, ts INTEGER NOT NULL,"
                    " severity TEXT DEFAULT 'info', detail TEXT, synced INTEGER DEFAULT 0)"
                )
            return True
        except sqlite3.Error:
            return False

    def append(self, etype: str, severity: str = "info", detail: str = None, ts: int = None) -> None:
        if not self._ok:
            return
        try:
            with self._connect() as c:
                c.execute(
                    "INSERT INTO telemetry_events (event_uid, type, ts, severity, detail)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, etype, int(ts or time.time()), severity, detail),
                )
        except sqlite3.Error:
            pass

    def unsynced(self, limit: int = 200) -> list:
        if not self._ok:
            return []
        try:
            with self._connect() as c:
                cur = c.execute(
                    "SELECT event_uid, type, ts, severity, detail FROM telemetry_events"
                    " WHERE synced = 0 ORDER BY ts ASC LIMIT ?",
                    (limit,),
                )
                return [
                    {"event_uid": r[0], "type": r[1], "ts": r[2], "severity": r[3], "detail": r[4]}
                    for r in cur.fetchall()
                ]
        except sqlite3.Error:
            return []

    def mark_synced(self, event_uids: list) -> None:
        if not self._ok or not event_uids:
            return
        try:
            with self._connect() as c:
                c.executemany(
                    "UPDATE telemetry_events SET synced = 1 WHERE event_uid = ?",
                    [(u,) for u in event_uids],
                )
        except sqlite3.Error:
            pass

    def prune_synced(self, keep_days: int = 30) -> None:
        """Housekeeping: drop long-since-synced rows so the table can't grow forever."""
        if not self._ok:
            return
        try:
            cutoff = int(time.time()) - keep_days * 86400
            with self._connect() as c:
                c.execute("DELETE FROM telemetry_events WHERE synced = 1 AND ts < ?", (cutoff,))
        except sqlite3.Error:
            pass


# ── Crash-vs-clean-restart marker ───────────────────────────────────

class RunMarker:
    """A file that exists while the process is running. If it's present at
    startup, the previous run did not shut down cleanly (crash / power loss).
    `previous_shutdown_clean()` MUST be read before `mark_running()` overwrites
    the on-disk state."""

    def __init__(self, path: str):
        self.path = path
        self._prev_present = os.path.exists(path)

    def previous_shutdown_clean(self) -> bool:
        # marker present at construction → prior run crashed; absent → clean/first boot
        return not self._prev_present

    def mark_running(self) -> None:
        try:
            with open(self.path, "w") as f:
                f.write(str(int(time.time())))
        except OSError:
            pass

    def mark_clean_shutdown(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


# ── Signed sender ───────────────────────────────────────────────────

class TelemetryClient:
    """Signs and posts heartbeat/events. Every network op is best-effort and
    time-boxed; a failure returns False and is never raised to the caller."""

    def __init__(self, uid: str, heartbeat_url: str, events_url: str, log_cb=None):
        self.uid = uid
        self.heartbeat_url = heartbeat_url
        self.events_url = events_url
        self._log = log_cb or (lambda msg: None)

    def _post_signed(self, url: str, payload: dict, secret: str, timeout: float = 8.0) -> bool:
        if not secret:
            return False
        try:
            import requests
            from api_client import JWTEncoder
            token = JWTEncoder.create_jwt(payload, secret)
            r = requests.post(url, json={"token": token}, timeout=timeout)
            return r.status_code == 200
        except Exception as e:  # noqa: BLE001 — telemetry must never raise
            self._log(f"[TELEMETRY] send failed: {e}")
            return False

    def send_heartbeat(self, status: dict, secret: str) -> bool:
        return self._post_signed(
            self.heartbeat_url, build_heartbeat_payload(self.uid, status), secret
        )

    def send_events(self, events: list, secret: str) -> bool:
        if not events:
            return True
        return self._post_signed(
            self.events_url, build_events_payload(self.uid, events), secret
        )
