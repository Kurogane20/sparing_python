"""
AQMS API Communication Module
Modul untuk komunikasi dengan server API dan JWT
"""

import json
import time
import base64
import hmac
import hashlib
from typing import Optional, Tuple
import requests
from requests.exceptions import RequestException, Timeout

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
        # Header
        header = {"alg": "HS256", "typ": "JWT"}
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
    
    def check_internet_connection(self) -> bool:
        """
        Cek koneksi internet
        """
        try:
            response = self._session.get(
                config.network.connection_check_url,
                timeout=5
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def fetch_secret_key(self, url: str) -> Tuple[str, bool]:
        """
        Ambil secret key dari server
        Args:
            url: URL endpoint secret key
        Returns:
            (secret_key, sukses)
        """
        try:
            response = self._session.get(url, timeout=10)
            
            if response.status_code == 200:
                secret_key = response.text.strip()
                print(f"[INFO] Secret key berhasil diambil dari {url}")
                return secret_key, True
            else:
                print(f"[ERROR] Gagal mengambil secret key. HTTP: {response.status_code}")
                return "", False
                
        except Timeout:
            print(f"[ERROR] Timeout mengambil secret key dari {url}")
            return "", False
        except RequestException as e:
            print(f"[ERROR] Request error: {e}")
            return "", False
    
    def fetch_all_secret_keys(self) -> bool:
        """
        Ambil semua secret key dari kedua server
        Returns: True jika minimal satu berhasil
        """
        success = False
        
        # Server 1
        key1, ok1 = self.fetch_secret_key(config.server.secret_key_url_1)
        if ok1:
            self.secret_key_1 = key1
            success = True
        else:
            self.secret_key_1 = "sparing1"  # Default
            print("[WARN] Menggunakan secret key default untuk server 1")
        
        # Server 2
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
                print(f"[INFO] Data berhasil dikirim ke {server_url}")
                print(f"[INFO] Response: {response.text[:200]}")
                return True
            else:
                print(f"[ERROR] Gagal kirim data. HTTP: {response.status_code}")
                print(f"[ERROR] Response: {response.text[:200]}")
                return False
                
        except Timeout:
            print(f"[ERROR] Timeout mengirim data ke {server_url}")
            return False
        except RequestException as e:
            print(f"[ERROR] Request error: {e}")
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
        # Server 1
        new_key1, ok1 = self.fetch_secret_key(config.server.secret_key_url_1)
        if ok1 and new_key1 != self.secret_key_1:
            print("[INFO] Secret key server 1 diperbarui")
            self.secret_key_1 = new_key1
        
        # Server 2
        new_key2, ok2 = self.fetch_secret_key(config.server.secret_key_url_2)
        if ok2 and new_key2 != self.secret_key_2:
            print("[INFO] Secret key server 2 diperbarui")
            self.secret_key_2 = new_key2
    
    @property
    def pending_backup_count(self) -> int:
        return len(self.backup_manager)
