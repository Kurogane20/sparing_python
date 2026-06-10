"""
AQMS API Communication Module
Modul untuk komunikasi dengan server API dan JWT
"""

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
    
    def __init__(self):
        self.secret_key_1: str = ""
        self.secret_key_2: str = ""
        self.backup_manager = DataBackupManager(config.data_backup_file)
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
            True jika berhasil
        """
        try:
            request_body = {"token": token}
            
            response = self._session.post(
                server_url,
                json=request_body,
                timeout=30
            )
            
            if response.status_code == 200:
                try:
                    body = response.json()
                    # Server 1 (Mitra Mutiara): {"rows":N, "message":"...", "uid":"...", "device_id":"..."}
                    # Server 2 (KLHK):          {"status":true/false, "desc":null/"..."}
                    if "status" in body:
                        ok   = body.get("status", False)
                        desc = body.get("desc") or "OK"
                        if ok:
                            self._write_log(
                                f"KIRIM OK  | {server_url} | {desc}",
                                short=f"OK  {server_url.split('/')[2]}"
                            )
                            return True
                        else:
                            self._write_log(
                                f"KIRIM GAGAL | {server_url} | HTTP 200 status=false | {desc}",
                                short=f"GAGAL  {str(desc)[:35]}"
                            )
                            return False
                    else:
                        rows   = body.get("rows", "?")
                        msg    = body.get("message", "OK")
                        uid    = body.get("uid", "-")
                        dev_id = body.get("device_id", "-")
                        self._write_log(
                            f"KIRIM OK  | {server_url} | rows={rows} | uid={uid} | device_id={dev_id} | msg={msg}",
                            short=f"OK  rows={rows}  uid={uid}"
                        )
                        return True
                except Exception:
                    self._write_log(
                        f"KIRIM OK  | {server_url} | raw={response.text[:120]}",
                        short=f"OK  {response.text[:40]}"
                    )
                    return True
            else:
                self._write_log(
                    f"KIRIM GAGAL | {server_url} | HTTP {response.status_code} | {response.text[:150]}",
                    short=f"GAGAL  HTTP {response.status_code}"
                )
                return False

        except Timeout:
            self._write_log(f"KIRIM GAGAL | {server_url} | TIMEOUT", short="GAGAL  TIMEOUT")
            return False
        except RequestException as e:
            self._write_log(f"KIRIM GAGAL | {server_url} | {e}", short=f"GAGAL  {str(e)[:40]}")
            return False
    
    def send_all_data(self, data_buffer: SensorDataBuffer) -> Tuple[bool, bool]:
        """
        Kirim data ke kedua server
        Args:
            data_buffer: Buffer data sensor
        Returns:
            (sukses_server1, sukses_server2)
        """
        # Cek koneksi internet
        if not self.check_internet_connection():
            print("[WARN] Tidak ada koneksi internet, menyimpan ke backup")
            self._save_to_backup(data_buffer)
            return False, False
        
        # Coba kirim data backup terlebih dahulu
        self._send_backup_data()
        
        # Buat JWT untuk server 1 (dengan power data dan device_id)
        token1 = self.create_jwt_token(
            config.server.uid_1,
            self.secret_key_1,
            data_buffer,
            include_power=True,
            device_id=config.server.device_id_1
        )
        
        # Buat JWT untuk server 2 (tanpa power data)
        token2 = self.create_jwt_token(
            config.server.uid_2,
            self.secret_key_2,
            data_buffer,
            include_power=False
        )
        
        if not token1 or not token2:
            print("[ERROR] Gagal membuat JWT token")
            return False, False
        
        # Kirim ke kedua server
        success1 = self.send_data(config.server.server_url_1, token1)
        success2 = self.send_data(config.server.server_url_2, token2)
        
        # Simpan ke backup jika gagal
        if not success1:
            self.backup_manager.add(BackupData(
                token=token1,
                server_url=config.server.server_url_1,
                timestamp=int(time.time())
            ))
        
        if not success2:
            self.backup_manager.add(BackupData(
                token=token2,
                server_url=config.server.server_url_2,
                timestamp=int(time.time())
            ))
        
        return success1, success2
    
    def _save_to_backup(self, data_buffer: SensorDataBuffer):
        """Simpan data ke backup file"""
        token1 = self.create_jwt_token(
            config.server.uid_1,
            self.secret_key_1,
            data_buffer,
            include_power=True,
            device_id=config.server.device_id_1
        )
        
        token2 = self.create_jwt_token(
            config.server.uid_2,
            self.secret_key_2,
            data_buffer,
            include_power=False
        )
        
        if token1:
            self.backup_manager.add(BackupData(
                token=token1,
                server_url=config.server.server_url_1,
                timestamp=int(time.time())
            ))
        
        if token2:
            self.backup_manager.add(BackupData(
                token=token2,
                server_url=config.server.server_url_2,
                timestamp=int(time.time())
            ))
        
        print(f"[INFO] Data disimpan ke backup ({len(self.backup_manager)} pending)")
    
    def _send_backup_data(self):
        """Kirim data yang tersimpan di backup"""
        if not self.backup_manager.has_pending_data():
            return
        
        print(f"[INFO] Mengirim {len(self.backup_manager)} data backup...")
        
        sent_items = []
        for backup in self.backup_manager.get_all():
            if self.send_data(backup.server_url, backup.token):
                sent_items.append(backup)
        
        # Hapus yang sudah terkirim
        for item in sent_items:
            self.backup_manager.remove(item)
        
        if sent_items:
            print(f"[INFO] {len(sent_items)} data backup berhasil dikirim")
    
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
