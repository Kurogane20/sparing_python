"""KLHK dan sensor COD bisa dimatikan per lokasi (config.json).

- server.klhk_enabled = False (uid KLHK belum terbit): tidak ada kiriman, key,
  maupun backup ke KLHK; antrean KLHK lama dibuang; status kirim KLHK = None
  ("tidak dicoba"), sehingga server monitoring tidak menyebutnya "gagal".
- modbus.cod_enabled = False (sensor COD tidak terpasang): COD tidak dibaca,
  dikirim 0.00, dan cod_ok = None, sehingga tidak ada alarm sensor COD.
"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import api_client
import sensors
from api_client import APIClient, failed_targets
from config import ModbusConfig, ServerConfig, _safe_load
from models import BackupData, DataBackupManager

MM = "https://mm.example/api/post-data"
KLHK = "https://klhk.example/api/send-hourly"
KLHK_KEY = "https://klhk.example/api/secret-sensor"


class FakeBuffer:
    def get_payload(self, uid, include_power=False, device_id=None):
        return {"uid": uid, "data": [{"datetime": 1}]}


def _item(url, server_num, n, uid="X-LOG"):
    return BackupData(server_url=url, timestamp=n,
                      payload={"uid": uid, "data": [{"datetime": n}]},
                      server_num=server_num)


class TestDefaults(unittest.TestCase):
    def test_klhk_and_cod_stay_on_unless_switched_off(self):
        self.assertTrue(ServerConfig().klhk_enabled)
        self.assertTrue(ModbusConfig().cod_enabled)
        # config.json lama tanpa kunci baru: tetap aktif
        self.assertTrue(_safe_load(ServerConfig, {"uid_2": "abc"}).klhk_enabled)
        self.assertTrue(_safe_load(ModbusConfig, {"cod_slave_id": 5}).cod_enabled)
        self.assertFalse(_safe_load(ServerConfig, {"klhk_enabled": False}).klhk_enabled)
        self.assertFalse(_safe_load(ModbusConfig, {"cod_enabled": False}).cod_enabled)


class TestFailedTargets(unittest.TestCase):
    def test_only_attempted_sends_can_fail(self):
        self.assertEqual(failed_targets(True, None), "")
        self.assertEqual(failed_targets(False, None), "MM")
        self.assertEqual(failed_targets(True, False), "KLHK")
        self.assertEqual(failed_targets(False, False), "MM+KLHK")
        self.assertEqual(failed_targets(True, True), "")


class TestPurgeServer(unittest.TestCase):
    def test_drops_one_servers_items_and_keeps_the_rest(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        try:
            mgr = DataBackupManager(path)
            mgr.add(_item(KLHK, 2, 1))
            mgr.add(_item(MM, 1, 2))
            mgr.add(BackupData(server_url=KLHK, timestamp=3, token="old.jwt.token"))  # format lama
            self.assertEqual(mgr.purge_server(2, KLHK), 2)
            self.assertEqual([b.server_url for b in DataBackupManager(path).get_all()], [MM])
            self.assertEqual(mgr.purge_server(2, KLHK), 0)
        finally:
            for p in (path, path.replace(".json", "_rejected.json")):
                if os.path.exists(p):
                    os.remove(p)


class TestKlhkSwitch(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        self.client = APIClient.__new__(APIClient)   # lewati __init__: tanpa file/config asli
        self.client.secret_key_1 = "k1"
        self.client.secret_key_2 = "k2"
        self.client.backup_manager = DataBackupManager(self.path)
        self.client._accepted_at = {}
        self.client._write_log = lambda *a, **k: None
        self.posted = []
        self.ok = {MM: True, KLHK: True}

        def fake_send(url, token):
            self.posted.append(url)
            return self.ok[url]

        def fake_post(url, token):
            self.posted.append(url)
            return "ok" if self.ok[url] else "error"

        self.client.send_data = fake_send
        self.client._post = fake_post
        self.client.check_internet_connection = lambda: True
        patcher = mock.patch.multiple(api_client.config.server, server_url_1=MM,
                                      server_url_2=KLHK, secret_key_url_2=KLHK_KEY,
                                      klhk_enabled=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for p in (self.path, self.client.backup_manager.rejected_file):
            if os.path.exists(p):
                os.remove(p)

    def _queued(self):
        return [(b.server_num, b.server_url) for b in self.client.backup_manager.get_all()]

    def test_off_sends_to_mm_only_and_reports_klhk_not_tried(self):
        self.assertEqual(self.client.send_all_data(FakeBuffer()), (True, None))
        self.assertEqual(self.posted, [MM])
        self.assertEqual(self._queued(), [])

    def test_off_backs_up_only_mm_when_mm_fails(self):
        self.ok[MM] = False
        self.assertEqual(self.client.send_all_data(FakeBuffer()), (False, None))
        self.assertEqual(self._queued(), [(1, MM)])

    def test_off_and_offline_backs_up_only_mm(self):
        self.client.check_internet_connection = lambda: False
        self.assertEqual(self.client.send_all_data(FakeBuffer()), (False, None))
        self.assertEqual(self._queued(), [(1, MM)])

    def test_off_drops_the_klhk_backlog_and_never_posts_it(self):
        for i in range(3):
            self.client.backup_manager.add(_item(KLHK, 2, i))
        self.client.backup_manager.add(_item(MM, 1, 100))
        self.client.retry_backup()
        self.assertNotIn(KLHK, self.posted)
        self.assertEqual(self._queued(), [])

    def test_off_never_asks_klhk_for_a_key(self):
        asked = []

        def fake_key(url, uid=""):
            asked.append(url)
            return "key", True

        self.client.fetch_secret_key = fake_key
        self.client.fetch_all_secret_keys()
        self.client.check_and_update_secret_keys()
        self.assertNotIn(KLHK_KEY, asked)
        self.assertEqual(self.client.secret_key_2, "")

    def test_on_still_sends_to_both(self):
        with mock.patch.object(api_client.config.server, "klhk_enabled", True):
            self.assertEqual(self.client.send_all_data(FakeBuffer()), (True, True))
        self.assertEqual(self.posted, [MM, KLHK])


class TestCodSwitch(unittest.TestCase):
    def setUp(self):
        self.reader = sensors.ModbusSensorReader.__new__(sensors.ModbusSensorReader)
        self.reader._log_callback = None
        self.reader.last_ok_count = -1
        self.reader.read_ph = lambda: (7.0, True)
        self.reader.read_tss = lambda: (5.0, True)
        self.reader.read_debit = lambda: (1.0, True)
        self.cod_reads = 0

        def read_cod():
            self.cod_reads += 1
            return 0.0, False

        self.reader.read_cod = read_cod
        for patcher in (mock.patch.object(sensors.time, "sleep"),
                        mock.patch.object(sensors.config.modbus, "nh3n_enabled", False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_off_cod_is_not_read_and_not_reported_as_failed(self):
        with mock.patch.object(sensors.config.modbus, "cod_enabled", False):
            data = self.reader.read_all_sensors()
        self.assertEqual(self.cod_reads, 0)
        self.assertEqual(data.cod, 0.0)
        self.assertIsNone(data.cod_ok)
        self.assertEqual(self.reader.last_ok_count, 3)   # 3 dari 3 sensor terpasang

    def test_on_cod_is_read_and_its_failure_counts(self):
        data = self.reader.read_all_sensors()
        self.assertEqual(self.cod_reads, 1)
        self.assertIs(data.cod_ok, False)
        self.assertEqual(self.reader.last_ok_count, 3)   # 3 dari 4


class TestGuiStatus(unittest.TestCase):
    def test_send_status_carries_not_tried(self):
        import gui
        got = []
        bridge = gui.SignalBridge()
        bridge.server_status_update.connect(lambda a, b: got.append((a, b)))
        bridge.server_status_update.emit(True, None)
        self.assertEqual(got, [(True, None)])
        self.assertEqual(gui.send_status_text(None, "10:00")[0], "NONAKTIF")
        self.assertEqual(gui.send_status_text(True, "10:00")[0], "OK 10:00")
        self.assertEqual(gui.send_status_text(False, "10:00")[0], "GAGAL 10:00")


if __name__ == "__main__":
    unittest.main()
