import os
import tempfile
import unittest

from telemetry import (
    build_status, _pct_from_meminfo, _disk_pct, clamp_pct,
    EventLog, RunMarker, build_heartbeat_payload, build_events_payload,
)


class TestStatusBuilder(unittest.TestCase):
    def test_build_status_shape_and_values(self):
        snap = {
            "uptime_s": 3600, "logger_version": "1.4.0", "op_status": 0,
            "sensor_ok": {"ph": True, "tss": False, "debit": True, "cod": None, "nh3n": None},
            "consec_fail": 2, "internet_ok": True,
            "last_send_ok_mm": True, "last_send_ok_klhk": False,
            "buffer_depth": 12, "daily_sent": 640,
            "cpu_temp": 52.3, "cpu_pct": 18.0, "mem_pct": 41.2, "disk_pct": 63.5,
        }
        st = build_status(snap)
        self.assertEqual(st["ph_ok"], True)
        self.assertEqual(st["tss_ok"], False)
        self.assertIsNone(st["cod_ok"])
        self.assertEqual(st["op_status"], 0)
        self.assertEqual(st["buffer_depth"], 12)
        self.assertEqual(st["logger_version"], "1.4.0")
        self.assertNotIn("sensor_ok", st)

    def test_clamp_pct(self):
        self.assertEqual(clamp_pct(-5), 0.0)
        self.assertEqual(clamp_pct(150), 100.0)
        self.assertEqual(clamp_pct(42.4), 42.4)

    def test_meminfo_parsing(self):
        sample = "MemTotal: 1000 kB\nMemAvailable: 250 kB\n"
        self.assertAlmostEqual(_pct_from_meminfo(sample), 75.0, places=1)

    def test_meminfo_garbage_returns_none(self):
        self.assertIsNone(_pct_from_meminfo("nonsense"))


class TestEventLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.log = EventLog(self.tmp)

    def tearDown(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def test_append_and_unsynced_roundtrip(self):
        self.log.append("started", severity="info", detail="clean=false")
        self.log.append("net_down", severity="warning")
        rows = self.log.unsynced(limit=200)
        self.assertEqual(len(rows), 2)
        uids = {r["event_uid"] for r in rows}
        self.assertEqual(len(uids), 2)
        self.assertTrue(all(r["type"] in ("started", "net_down") for r in rows))

    def test_mark_synced_removes_from_unsynced(self):
        self.log.append("started")
        self.log.append("net_up")
        rows = self.log.unsynced()
        self.log.mark_synced([r["event_uid"] for r in rows])
        self.assertEqual(self.log.unsynced(), [])

    def test_unsynced_survives_reopen(self):
        self.log.append("buffer_high")
        reopened = EventLog(self.tmp)
        self.assertEqual(len(reopened.unsynced()), 1)

    def test_append_never_raises_on_bad_db(self):
        broken = EventLog("/nonexistent-dir/telemetry.db")
        broken.append("started")
        self.assertEqual(broken.unsynced(), [])


class TestRunMarker(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".marker")

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_first_ever_boot_is_treated_as_clean(self):
        m = RunMarker(self.path)
        self.assertTrue(m.previous_shutdown_clean())
        m.mark_running()

    def test_running_marker_present_means_prior_crash(self):
        m = RunMarker(self.path)
        m.mark_running()
        m2 = RunMarker(self.path)
        self.assertFalse(m2.previous_shutdown_clean())

    def test_clean_shutdown_clears_marker(self):
        m = RunMarker(self.path)
        m.mark_running()
        m.mark_clean_shutdown()
        m2 = RunMarker(self.path)
        self.assertTrue(m2.previous_shutdown_clean())


class TestPayloadBuilders(unittest.TestCase):
    def test_heartbeat_payload_wraps_uid_and_status(self):
        p = build_heartbeat_payload("admin-LOG", {"buffer_depth": 3})
        self.assertEqual(p["uid"], "admin-LOG")
        self.assertEqual(p["status"]["buffer_depth"], 3)

    def test_events_payload_wraps_uid_and_list(self):
        evs = [{"event_uid": "a", "type": "started", "ts": 1}]
        p = build_events_payload("admin-LOG", evs)
        self.assertEqual(p["uid"], "admin-LOG")
        self.assertEqual(p["events"], evs)


if __name__ == "__main__":
    unittest.main()
