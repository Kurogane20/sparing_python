"""
AQMS API Communication Module
Modul untuk komunikasi dengan server API dan JWT
"""

import os
import json
import time
import base64
import hmac
import hashlib
import socket
from datetime import datetime
from typing import Optional, Tuple
import requests
from requests.exceptions import RequestException, Timeout

LOG_FILE = "transmission_log.txt"

from config import config
from models import SensorDataBuffer, BackupData, DataBackupManager

class JWTEncoder:
    """
    Encoder JWT sederhana dengan algoritma HS256
    Kompatibel dengan ArduinoJWT
    """
    
    @staticmethod
    def base64url_encode(data: bytes) -> str:
        """Encode bytes ke base64url (tanpa padding)"""
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')
    
    @staticmethod
    def create_jwt(payload: dict, secret: str) -> str:
        """
        Buat JWT token dengan HS256
        Args:
            payload: Dictionary payload
            secret: Secret key untuk signing
        Returns:
            JWT token string
        """
        # Header — urutan typ dulu sesuai spek SPARING
        header = {"typ": "JWT", "alg": "HS256"}
        header_encoded = JWTEncoder.base64url_encode(
            json.dumps(header, separators=(',', ':')).encode('utf-8')
        )
        
        # Payload
        payload_encoded = JWTEncoder.base64url_encode(
            json.dumps(payload, separators=(',', ':')).encode('utf-8')
        )
        
        # Signature
        message = f"{header_encoded}.{payload_encoded}"
        signature = hmac.new(
            secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        signature_encoded = JWTEncoder.base64url_encode(signature)
        
        return f"{message}.{signature_encoded}"


class APIClient:
    """
    Client untuk komunikasi dengan server API SPARING
    """

    # Item backup yang ditolak permanen sebanyak ini (saat server terbukti
    # menerima data lain) dipindah ke file rejected
    MAX_REJECTS = 6
    # Server dianggap "sehat untuk perangkat ini" jika menerima data dalam
    # jendela ini — penolakan di luar jendela bisa masalah konfigurasi
    REJECT_PROOF_WINDOW = 600  # detik
    # Batas lama satu siklus kirim backup (jaga jadwal baca sensor 2 menit)
    BACKUP_TIME_BUDGET = 60  # detik
    # KLHK menolak payload yang seluruh datanya sudah ada di server
    _DUPLICATE_MARKERS = ("duplikasi data", "sudah terkirim")

    def __init__(self):
        self.secret_key_1: str = ""
        self.secret_key_2: str = ""
        self._accepted_at: dict = {}  # server_url → waktu terakhir data diterima
        self.backup_manager = DataBackupManager(config.data_backup_file)
        # Backup format lama (token jadi) → payload, agar ditandatangani ulang
        # dengan key terkini dan uid-nya bisa dinilai purge_foreign_uids
        _upgraded = self.backup_manager.upgrade_legacy_tokens({
            config.server.server_url_1: 1, config.server.server_url_2: 2})
        if _upgraded:
            print(f"[INFO] {_upgraded} backup format lama dikonversi ke payload")
        # Auto-bersihkan backup dengan uid lama (mis. setelah uid diganti):
        # payload-nya membawa uid lama, jadi akan ditolak server selamanya dan
        # hanya membebani antrean retry. Dibuang saat startup.
        _dropped = self.backup_manager.purge_foreign_uids(
            {config.server.uid_1, config.server.uid_2})
        if _dropped:
            print(f"[INFO] {_dropped} backup uid lama dibersihkan (uid berganti)")
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        self._log_callback = None

    def set_log_callback(self, cb):
        """Daftarkan callback untuk menampilkan log di GUI (dipanggil dari main thread)."""
        self._log_callback = cb

    def _write_log(self, line: str, short: str = ""):
        """Tulis log ke file dan terminal; kirim versi ringkas ke GUI via callback."""
        ts    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ts_s  = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {line}"
        print(entry)
        try:
            # Rotasi log: maks 1 MB, simpan 1 file lama —
            # cegah file tumbuh tanpa batas (umur SD card RPi)
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
                os.replace(LOG_FILE, LOG_FILE + ".old")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:
            pass
        if self._log_callback:
            self._log_callback(f"{ts_s}  {short or line[:55]}")
    
    def check_internet_connection(self) -> bool:
        """
        Cek koneksi internet via TCP ke DNS Google (8.8.8.8:53).
        Tidak menggunakan HTTP/HTTPS — bebas dari masalah SSL dan redirect.
        """
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=5)
            return True
        except OSError:
            return False
    
    def fetch_secret_key(self, url: str, uid: str = "") -> Tuple[str, bool]:
        """
        Ambil secret key dari server.
        Spec Server 1: GET /api/get-key?uid=SITE_UID
        """
        try:
            params = {"uid": uid} if uid else {}
            response = self._session.get(url, params=params, timeout=10)

            if response.status_code == 200:
                raw = response.text.strip()
                # Tangani jika server mengembalikan JSON
                try:
                    data = json.loads(raw)
                    if isinstance(data, str):
                        secret_key = data
                    elif isinstance(data, dict):
                        secret_key = data.get("secret") or data.get("key") or data.get("data") or raw
                    else:
                        secret_key = raw
                except (json.JSONDecodeError, ValueError):
                    secret_key = raw
                preview = secret_key[:6] + "..." if len(secret_key) > 6 else secret_key
                print(f"[INFO] Secret key berhasil diambil (uid={uid}): '{preview}' len={len(secret_key)}")
                return secret_key, True
            else:
                print(f"[ERROR] Gagal mengambil secret key. HTTP: {response.status_code} | {response.text[:80]}")
                return "", False

        except Timeout:
            print(f"[ERROR] Timeout mengambil secret key dari {url}")
            return "", False
        except RequestException as e:
            print(f"[ERROR] Request error: {e}")
            return "", False

    def fetch_all_secret_keys(self) -> bool:
        """
        Ambil semua secret key dari kedua server.
        UID dikirim sebagai query parameter sesuai spesifikasi.
        Returns: True jika minimal satu berhasil
        """
        success = False

        # Server 1 — GET /api/get-key?uid=uid_1
        key1, ok1 = self.fetch_secret_key(config.server.secret_key_url_1, config.server.uid_1)
        if ok1:
            self.secret_key_1 = key1
            success = True
        else:
            self.secret_key_1 = "sparing1"  # Default
            print("[WARN] Menggunakan secret key default untuk server 1")

        # Server 2 — spec belum tersedia, uid tidak dikirim
        key2, ok2 = self.fetch_secret_key(config.server.secret_key_url_2)
        if ok2:
            self.secret_key_2 = key2
            success = True
        else:
            self.secret_key_2 = "sparing2"  # Default
            print("[WARN] Menggunakan secret key default untuk server 2")

        return success
    
    def create_jwt_token(self, uid: str, secret_key: str, 
                         data_buffer: SensorDataBuffer, 
                         include_power: bool = False,
                         device_id: str = None) -> str:
        """
        Buat JWT token untuk pengiriman data
        """
        if not secret_key:
            print("[ERROR] Secret key kosong, tidak bisa membuat JWT")
            return ""
        
        payload = data_buffer.get_payload(uid, include_power, device_id)
        return JWTEncoder.create_jwt(payload, secret_key)
    
    def send_data(self, server_url: str, token: str) -> bool:
        """
        Kirim data ke server
        Args:
            server_url: URL server tujuan
            token: JWT token
        Returns:
            True jika diterima (atau datanya memang sudah ada di server)
        """
        delivered = self._post(server_url, token) in ("ok", "duplicate")
        if delivered:
            self._accepted_at[server_url] = time.time()
        return delivered

    def _post(self, server_url: str, token: str) -> str:
        """Kirim satu token JWT dan klasifikasikan jawabannya:
          "ok"        — diterima
          "duplicate" — ditolak karena datanya sudah ada (anggap terkirim)
          "rejected"  — server menolak item ini (HTTP 4xx / status=false);
                        mengirim ulang item yang sama tidak akan berhasil
          "error"     — gangguan sementara (timeout, koneksi, 5xx, 408, 429)
        """
        try:
            response = self._session.post(
                server_url,
                json={"token": token},
                timeout=30
            )
        except Timeout:
            self._write_log(f"KIRIM GAGAL | {server_url} | TIMEOUT", short="GAGAL  TIMEOUT")
            return "error"
        except RequestException as e:
            self._write_log(f"KIRIM GAGAL | {server_url} | {e}", short=f"GAGAL  {str(e)[:40]}")
            return "error"

        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = None

        if status == 200:
            # Server 1 (Mitra Mutiara): {"rows":N, "message":"...", "uid":"...", "device_id":"..."}
            # Server 2 (KLHK):          {"status":true/false, "desc":null/"..."}
            if body is None:
                self._write_log(
                    f"KIRIM OK  | {server_url} | raw={response.text[:120]}",
                    short=f"OK  {response.text[:40]}"
                )
                return "ok"
            if "status" not in body:
                rows   = body.get("rows", "?")
                msg    = body.get("message", "OK")
                uid    = body.get("uid", "-")
                dev_id = body.get("device_id", "-")
                self._write_log(
                    f"KIRIM OK  | {server_url} | rows={rows} | uid={uid} | device_id={dev_id} | msg={msg}",
                    short=f"OK  rows={rows}  uid={uid}"
                )
                return "ok"
            if body.get("status"):
                self._write_log(
                    f"KIRIM OK  | {server_url} | {body.get('desc') or 'OK'}",
                    short=f"OK  {server_url.split('/')[2]}"
                )
                return "ok"
            # status=false → diperlakukan sebagai penolakan di bawah

        detail = f"HTTP {status} | {response.text[:150]}"
        if status == 200 or 400 <= status < 500:
            if any(m in response.text.lower() for m in self._DUPLICATE_MARKERS):
                self._write_log(
                    f"KIRIM OK  | {server_url} | duplikat, data sudah ada di server | {detail}",
                    short=f"OK  {server_url.split('/')[2]} (sudah ada)"
                )
                return "duplicate"
            if status not in (408, 429):
                self._write_log(
                    f"KIRIM GAGAL | {server_url} | {detail}",
                    short=f"DITOLAK  HTTP {status}"
                )
                return "rejected"
        self._write_log(f"KIRIM GAGAL | {server_url} | {detail}", short=f"GAGAL  HTTP {status}")
        return "error"

    def _recently_accepted(self, server_url: str) -> bool:
        """Server ini menerima data perangkat ini dalam REJECT_PROOF_WINDOW
        terakhir — uid/secret terbukti valid, jadi penolakan = itemnya rusak."""
        last = self._accepted_at.get(server_url)
        return last is not None and time.time() - last <= self.REJECT_PROOF_WINDOW
    
    def _build_payloads(self, data_buffer: SensorDataBuffer) -> Tuple[dict, dict]:
        """Buat payload mentah untuk kedua server."""
        payload1 = data_buffer.get_payload(
            config.server.uid_1, include_power=True,
            device_id=config.server.device_id_1
        )
        payload2 = data_buffer.get_payload(config.server.uid_2, include_power=False)
        return payload1, payload2

    def _secret_for(self, server_num: int) -> str:
        return self.secret_key_1 if server_num == 1 else self.secret_key_2

    def send_all_data(self, data_buffer: SensorDataBuffer) -> Tuple[bool, bool]:
        """
        Kirim data ke kedua server
        Args:
            data_buffer: Buffer data sensor
        Returns:
            (sukses_server1, sukses_server2)
        """
        payload1, payload2 = self._build_payloads(data_buffer)

        # Cek koneksi internet
        if not self.check_internet_connection():
            print("[WARN] Tidak ada koneksi internet, menyimpan ke backup")
            self._backup_payload(payload1, 1)
            self._backup_payload(payload2, 2)
            return False, False

        # Coba kirim data backup terlebih dahulu
        self._send_backup_data()

        token1 = JWTEncoder.create_jwt(payload1, self.secret_key_1) if self.secret_key_1 else ""
        token2 = JWTEncoder.create_jwt(payload2, self.secret_key_2) if self.secret_key_2 else ""

        # Kirim ke kedua server (token kosong = gagal → masuk backup)
        success1 = bool(token1) and self.send_data(config.server.server_url_1, token1)
        success2 = bool(token2) and self.send_data(config.server.server_url_2, token2)

        # Simpan payload mentah ke backup jika gagal —
        # token dibuat ulang saat retry dengan key terkini
        if not success1:
            self._backup_payload(payload1, 1)
        if not success2:
            self._backup_payload(payload2, 2)

        return success1, success2

    def _backup_payload(self, payload: dict, server_num: int):
        """Simpan payload mentah ke backup file."""
        url = config.server.server_url_1 if server_num == 1 else config.server.server_url_2
        self.backup_manager.add(BackupData(
            server_url=url,
            timestamp=int(time.time()),
            payload=payload,
            server_num=server_num
        ))
        print(f"[INFO] Payload server {server_num} disimpan ke backup "
              f"({len(self.backup_manager)} pending)")

    def _send_backup_data(self, max_items: int = 5):
        """Kirim data yang tersimpan di backup.
        Token JWT dibuat BARU di sini dengan secret key terkini.

        max_items membatasi jumlah PERCOBAAN kirim per server per siklus dan
        BACKUP_TIME_BUDGET membatasi lama satu siklus, agar worker thread
        tidak terblokir lama (timeout 30 dtk/item bisa melewatkan jadwal
        baca sensor). Sisa backup dikirim di siklus retry berikutnya.

        Seluruh antrean dipindai (bukan hanya max_items item tertua): item
        ke server yang sedang gangguan di siklus ini dilewati, sehingga
        backup server yang terus gagal (mis. KLHK) tidak menahan backup
        server lain di belakangnya sampai ~16 hari.

        Item yang DITOLAK (HTTP 4xx) oleh server yang baru saja menerima data
        lain adalah item rusak (uid/format salah) — ia tidak menahan item lain
        ke server yang sama, dan setelah MAX_REJECTS kali dipindah ke file
        rejected. Tanpa bukti server menerima data, penolakan bisa berarti
        masalah konfigurasi (site belum terdaftar) — server dilewati saja.
        """
        if not self.backup_manager.has_pending_data():
            return

        print(f"[INFO] Mengirim data backup ({len(self.backup_manager)} pending)...")

        deadline = time.monotonic() + self.BACKUP_TIME_BUDGET
        sent_items = []
        rejected_items = []
        failed_servers = set()
        attempts = {}
        for backup in self.backup_manager.get_all():
            url = backup.server_url
            # Server ini baru saja gagal — item lain ke server yang sama
            # hampir pasti gagal juga; jangan buang waktu timeout
            if url in failed_servers or attempts.get(url, 0) >= max_items:
                continue
            if backup.payload:
                secret = self._secret_for(backup.server_num)
                if not secret:
                    continue  # key belum tersedia, coba lagi nanti
                token = JWTEncoder.create_jwt(backup.payload, secret)
            else:
                token = backup.token  # format lama: token jadi
            if not token:
                continue
            if time.monotonic() >= deadline:
                break
            attempts[url] = attempts.get(url, 0) + 1
            result = self._post(url, token)
            if result in ("ok", "duplicate"):
                self._accepted_at[url] = time.time()
                sent_items.append(backup)
            elif result == "rejected" and self._recently_accepted(url):
                rejected_items.append(backup)
            else:
                failed_servers.add(url)

        # Hapus yang sudah terkirim
        for item in sent_items:
            self.backup_manager.remove(item)

        if sent_items:
            print(f"[INFO] {len(sent_items)} data backup berhasil dikirim "
                  f"({len(self.backup_manager)} tersisa)")

        if rejected_items:
            for item in rejected_items:
                item.rejects += 1
            self.backup_manager.save()
            dead = [b for b in rejected_items if b.rejects >= self.MAX_REJECTS]
            if dead:
                self.backup_manager.dead_letter(dead)
                self._write_log(
                    f"BACKUP DITOLAK | {len(dead)} item ditolak {self.MAX_REJECTS}x, "
                    f"dipindah ke {self.backup_manager.rejected_file}",
                    short=f"{len(dead)} backup ditolak permanen"
                )

    def retry_backup(self):
        """Public: coba kirim ulang backup — dipanggil worker secara berkala."""
        self._send_backup_data()
    
    def check_and_update_secret_keys(self):
        """Cek dan update secret key jika berubah"""
        new_key1, ok1 = self.fetch_secret_key(config.server.secret_key_url_1, config.server.uid_1)
        if ok1 and new_key1 != self.secret_key_1:
            print("[INFO] Secret key server 1 diperbarui")
            self.secret_key_1 = new_key1

        new_key2, ok2 = self.fetch_secret_key(config.server.secret_key_url_2)
        if ok2 and new_key2 != self.secret_key_2:
            print("[INFO] Secret key server 2 diperbarui")
            self.secret_key_2 = new_key2
    
    @property
    def pending_backup_count(self) -> int:
        return len(self.backup_manager)
