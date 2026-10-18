import json
import os
import tempfile
import time
import unittest
from unittest import mock

import api_client
from api_client import APIClient, JWTEncoder
from models import BackupData, DataBackupManager, decode_jwt_payload
from requests.exceptions import Timeout

MM = "https://mm.example/api/post-data"
KLHK = "https://klhk.example/api/post-data"
POISON = -1  # datetime penanda item yang selalu ditolak server


def _item(url, server_num, n, uid="X-LOG"):
    return BackupData(server_url=url, timestamp=n,
                      payload={"uid": uid, "data": [{"datetime": n}]},
                      server_num=server_num)


class TestBackupRetry(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        # Lewati __init__ agar tidak menyentuh data_backup.json / config asli
        self.client = APIClient.__new__(APIClient)
        self.client.secret_key_1 = "k1"
        self.client.secret_key_2 = "k2"
        self.client.backup_manager = DataBackupManager(self.path)
        self.client._accepted_at = {}
        self.client._write_log = lambda *a, **k: None  # jangan tulis log file
        self.attempts = []
        # Default: MM menerima, KLHK down (gangguan sementara)
        self.responses = {MM: "ok", KLHK: "error"}

        def fake_post(url, token):
            self.attempts.append(url)
            if decode_jwt_payload(token)["data"][0]["datetime"] == POISON:
                return "rejected"
            return self.responses[url]
        self.client._post = fake_post

    def tearDown(self):
        for p in (self.path, self.client.backup_manager.rejected_file):
            if os.path.exists(p):
                os.remove(p)

    def _add(self, url, server_num, n):
        self.client.backup_manager.add(_item(url, server_num, n))

    def _remaining(self):
        return [b.timestamp for b in self.client.backup_manager.get_all()]

    def test_failing_server_does_not_block_other_server(self):
        # Backup KLHK tertua (selalu gagal) menutupi backup MM di belakangnya
        for i in range(6):
            self._add(KLHK, 2, i)
        self._add(MM, 1, 100)
        self.client.retry_backup()
        remaining = [b.server_url for b in self.client.backup_manager.get_all()]
        self.assertNotIn(MM, remaining)
        self.assertEqual(remaining.count(KLHK), 6)

    def test_failed_server_tried_once_per_cycle(self):
        for i in range(6):
            self._add(KLHK, 2, i)
        self._add(MM, 1, 100)
        self.client.retry_backup()
        self.assertEqual(self.attempts.count(KLHK), 1)

    def test_attempts_capped_and_oldest_first(self):
        for i in range(8):
            self._add(MM, 1, i)
        self.client.retry_backup()
        self.assertEqual(len(self.attempts), 5)
        self.assertEqual(self._remaining(), [5, 6, 7])

    def test_attempt_budget_is_per_server(self):
        # Antrean KLHK panjang tidak menghabiskan jatah percobaan MM
        self.responses[KLHK] = "ok"
        for i in range(6):
            self._add(KLHK, 2, i)
        for i in range(100, 103):
            self._add(MM, 1, i)
        self.client.retry_backup()
        self.assertEqual(self.attempts.count(KLHK), 5)
        self.assertEqual(self.attempts.count(MM), 3)

    def test_poison_item_does_not_block_server_that_accepts_data(self):
        # FSK-LOG: item tertua ditolak permanen (401 Invalid UID) sementara
        # server menerima data lain — item di belakangnya harus tetap terkirim
        self.client._accepted_at[MM] = time.time()
        self._add(MM, 1, POISON)
        for i in range(1, 4):
            self._add(MM, 1, i)
        self.client.retry_backup()
        self.assertEqual(self._remaining(), [POISON])
        self.assertEqual(self.client.backup_manager.get_all()[0].rejects, 1)

    def test_rejection_without_recent_acceptance_blocks_server(self):
        # Server menolak tapi belum terbukti menerima apa pun (uid/site belum
        # terdaftar?) — bisa jadi masalah konfigurasi, bukan item: jangan
        # hitung penolakan dan jangan coba item lain di siklus ini
        self._add(MM, 1, POISON)
        for i in range(1, 4):
            self._add(MM, 1, i)
        self.client.retry_backup()
        self.assertEqual(self.attempts, [MM])
        self.assertEqual(self._remaining(), [POISON, 1, 2, 3])
        self.assertEqual(self.client.backup_manager.get_all()[0].rejects, 0)

    def test_poison_moved_to_rejected_file_after_max_rejects(self):
        self.client._accepted_at[MM] = time.time()
        self._add(MM, 1, POISON)
        self.client.backup_manager.get_all()[0].rejects = APIClient.MAX_REJECTS - 1
        self._add(MM, 1, 1)
        self.client.retry_backup()
        self.assertEqual(self._remaining(), [])
        with open(self.client.backup_manager.rejected_file) as f:
            dead = json.load(f)
        self.assertEqual([d["timestamp"] for d in dead], [POISON])
        # Perubahan antrean tersimpan ke disk — bertahan setelah restart
        reloaded = DataBackupManager(self.path)
        self.assertEqual(len(reloaded), 0)

    def test_rejects_persist_across_restart(self):
        self.client._accepted_at[MM] = time.time()
        self._add(MM, 1, POISON)
        self.client.retry_backup()
        reloaded = DataBackupManager(self.path)
        self.assertEqual(reloaded.get_all()[0].rejects, 1)

    def test_duplicate_counts_as_delivered(self):
        # KLHK: "duplikasi data atau data sudah terkirim" → data sudah ada
        self.responses[KLHK] = "duplicate"
        self._add(KLHK, 2, 1)
        self.client.retry_backup()
        self.assertEqual(self._remaining(), [])

    def test_cycle_stops_after_time_budget(self):
        # Respons lambat tidak boleh menahan worker (jadwal baca sensor)
        clock = [0.0]

        def slow_post(url, token):
            self.attempts.append(url)
            clock[0] += 25
            return "ok"
        self.client._post = slow_post
        for i in range(5):
            self._add(MM, 1, i)
        with mock.patch.object(api_client.time, "monotonic", lambda: clock[0]):
            self.client.retry_backup()
        self.assertEqual(len(self.attempts), 3)


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class TestPostClassification(unittest.TestCase):
    def setUp(self):
        self.client = APIClient.__new__(APIClient)
        self.client._accepted_at = {}
        self.client._write_log = lambda *a, **k: None  # jangan tulis log file
        self.client._session = mock.Mock()

    def _classify(self, status, body):
        self.client._session.post.return_value = _Resp(status, body)
        return self.client._post(MM, "tok")

    def test_mm_rows_ok(self):
        self.assertEqual(self._classify(200, {"rows": 30, "message": "ok"}), "ok")

    def test_klhk_status_true_ok(self):
        self.assertEqual(self._classify(200, {"status": True, "desc": None}), "ok")

    def test_status_false_rejected(self):
        self.assertEqual(self._classify(200, {"status": False, "desc": "x"}), "rejected")

    def test_klhk_duplicate(self):
        body = {"status": False, "data": None, "desc": (
            "Seluruh data yang dikirim gagal disimpan dikarenakan terdapat "
            "duplikasi data atau data sudah terkirim.")}
        self.assertEqual(self._classify(400, body), "duplicate")

    def test_4xx_rejected(self):
        self.assertEqual(self._classify(401, {"detail": "Invalid UID"}), "rejected")
        self.assertEqual(self._classify(400, {"desc": "Id logger not valid."}), "rejected")

    def test_transient_errors(self):
        self.assertEqual(self._classify(500, "Internal Server Error"), "error")
        self.assertEqual(self._classify(502, "<html>Bad Gateway</html>"), "error")
        self.assertEqual(self._classify(429, {"detail": "slow down"}), "error")

    def test_timeout_is_error(self):
        self.client._session.post.side_effect = Timeout()
        self.assertEqual(self.client._post(MM, "tok"), "error")

    def test_send_data_true_on_duplicate_and_records_acceptance(self):
        self.client._post = lambda url, token: "duplicate"
        self.assertTrue(self.client.send_data(KLHK, "tok"))
        self.assertIn(KLHK, self.client._accepted_at)

    def test_send_data_false_on_rejection(self):
        self.client._post = lambda url, token: "rejected"
        self.assertFalse(self.client.send_data(MM, "tok"))
        self.assertNotIn(MM, self.client._accepted_at)


class TestLegacyUpgrade(unittest.TestCase):
    """Backup format lama (token JWT jadi, tanpa payload) — dekode payload-nya
    agar bisa ditandatangani ulang dengan key terkini / dibersihkan jika uid
    lama. Kasus nyata FSK-LOG: token uid admin-LOG di kepala antrean ditolak
    401 tiap 5 menit dan menahan 100+ backup MM di belakangnya."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        self.mgr = DataBackupManager(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _legacy(self, url, uid):
        payload = {"uid": uid, "device_id": "DEVICE-001", "data": [{"datetime": 7}]}
        token = JWTEncoder.create_jwt(payload, "old-secret")
        self.mgr.add(BackupData(server_url=url, timestamp=7, token=token))
        return payload

    def test_decode_jwt_payload(self):
        tok = JWTEncoder.create_jwt({"uid": "A", "data": []}, "s")
        self.assertEqual(decode_jwt_payload(tok), {"uid": "A", "data": []})
        self.assertIsNone(decode_jwt_payload("garbage"))
        self.assertIsNone(decode_jwt_payload("a.!!!.c"))

    def test_upgrade_converts_token_to_payload(self):
        payload = self._legacy(MM, "FSK-LOG")
        n = self.mgr.upgrade_legacy_tokens({MM: 1, KLHK: 2})
        self.assertEqual(n, 1)
        b = self.mgr.get_all()[0]
        self.assertEqual((b.payload, b.server_num, b.token), (payload, 1, ""))
        # Tersimpan ke disk
        self.assertEqual(DataBackupManager(self.path).get_all()[0].payload, payload)

    def test_upgrade_skips_unknown_url_and_garbage(self):
        self._legacy("https://old.example/api", "FSK-LOG")
        self.mgr.add(BackupData(server_url=MM, timestamp=8, token="not-a-jwt"))
        self.assertEqual(self.mgr.upgrade_legacy_tokens({MM: 1, KLHK: 2}), 0)
        self.assertTrue(all(b.payload is None for b in self.mgr.get_all()))

    def test_upgrade_then_purge_drops_foreign_uid(self):
        self._legacy(MM, "admin-LOG")
        self._legacy(MM, "FSK-LOG")
        self.mgr.upgrade_legacy_tokens({MM: 1, KLHK: 2})
        self.assertEqual(self.mgr.purge_foreign_uids({"FSK-LOG", "klhk-uid"}), 1)
        self.assertEqual([b.payload["uid"] for b in self.mgr.get_all()], ["FSK-LOG"])


if __name__ == "__main__":
    unittest.main()
