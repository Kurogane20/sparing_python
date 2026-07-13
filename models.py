"""
AQMS Data Models
Model data untuk sensor dan pengiriman
"""

from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
from enum import IntEnum
import threading
import json


class OperationalStatus(IntEnum):
    NORMAL      =  0
    STOPPED     = -1  # Produksi berhenti sementara  → kirim -1
    CALIBRATION = -2  # Kalibrasi / audit             → kirim -2
    MALFUNCTION = -3  # Alat rusak / tidak optimal    → kirim -3


class OperationalState:
    """Thread-safe singleton status operasional RTU (Pasal 6.2.6.6g).
    Status di-persist ke file agar tidak hilang saat restart/mati listrik —
    misal status KALIBRASI harus tetap aktif setelah listrik kembali."""
    _status = OperationalStatus.NORMAL
    _lock = threading.Lock()
    _file = "operational_status.json"

    @classmethod
    def get(cls) -> OperationalStatus:
        with cls._lock:
            return cls._status

    @classmethod
    def set(cls, status: OperationalStatus) -> None:
        with cls._lock:
            cls._status = status
        cls._save()

    @classmethod
    def is_normal(cls) -> bool:
        return cls.get() == OperationalStatus.NORMAL

    @classmethod
    def _save(cls) -> None:
        try:
            with open(cls._file, 'w') as f:
                json.dump({"status": int(cls.get())}, f)
        except Exception as e:
            print(f"[ERROR] Gagal menyimpan status operasional: {e}")

    @classmethod
    def load(cls) -> None:
        """Pulihkan status terakhir dari file — panggil saat aplikasi start."""
        try:
            with open(cls._file, 'r') as f:
                data = json.load(f)
            with cls._lock:
                cls._status = OperationalStatus(data.get("status", 0))
            if cls._status != OperationalStatus.NORMAL:
                print(f"[INFO] Status operasional dipulihkan: {cls._status.name}")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[ERROR] Gagal memuat status operasional: {e}")

@dataclass
class SensorData:
    """Model data sensor tunggal"""
    ph: float = 0.0
    tss: float = 0.0
    debit: float = 0.0
    current: float = 0.0  # Arus dalam Ampere
    voltage: float = 0.0  # Tegangan dalam Volt
    cod: float = 0.0
    nh3n: float = 0.0
    timestamp: int = 0  # Unix timestamp

    # Status baca per sensor untuk indikator LED di GUI
    # (None = sensor tidak tersedia, True = OK, False = gagal dibaca)
    ph_ok: Optional[bool] = None
    tss_ok: Optional[bool] = None
    debit_ok: Optional[bool] = None
    cod_ok: Optional[bool] = None
    nh3n_ok: Optional[bool] = None
    
    def to_dict(self) -> dict:
        """Konversi ke dictionary untuk JSON.
        Pasal 6.2.6.6g: kirim kode kondisi (-1/-2/-3) saat status tidak normal.
        """
        status = OperationalState.get()
        if status != OperationalStatus.NORMAL:
            code = int(status)
            return {
                "datetime": self.timestamp,
                "pH": code, "tss": code, "debit": code,
                "cod": code, "nh3n": code,
            }
        return {
            "datetime": self.timestamp,
            "pH": round(self.ph, 2),
            "tss": round(self.tss, 2),
            "debit": round(self.debit, 2),
            "cod": round(self.cod, 2),
            "nh3n": round(self.nh3n, 2),
        }

    def to_dict_with_power(self) -> dict:
        """Konversi ke dictionary dengan data arus dan tegangan.
        Pasal 6.2.6.6g: parameter kualitas air diganti kode kondisi saat tidak normal.
        """
        status = OperationalState.get()
        if status != OperationalStatus.NORMAL:
            code = int(status)
            return {
                "datetime": self.timestamp,
                "pH": code, "tss": code, "debit": code,
                "cod": code, "nh3n": code,
                "current": round(self.current, 2),
                "voltage": round(self.voltage, 2),
            }
        return {
            "datetime": self.timestamp,
            "pH": round(self.ph, 2),
            "tss": round(self.tss, 2),
            "debit": round(self.debit, 2),
            "current": round(self.current, 2),
            "voltage": round(self.voltage, 2),
            "cod": round(self.cod, 2),
            "nh3n": round(self.nh3n, 2),
        }
    
    def to_raw_dict(self) -> dict:
        """Serialisasi mentah untuk cache disk — tanpa transformasi status."""
        return {
            "ph": self.ph, "tss": self.tss, "debit": self.debit,
            "current": self.current, "voltage": self.voltage,
            "cod": self.cod, "nh3n": self.nh3n, "timestamp": self.timestamp,
        }

    @classmethod
    def from_raw_dict(cls, d: dict) -> 'SensorData':
        return cls(
            ph=d.get("ph", 0.0), tss=d.get("tss", 0.0), debit=d.get("debit", 0.0),
            current=d.get("current", 0.0), voltage=d.get("voltage", 0.0),
            cod=d.get("cod", 0.0), nh3n=d.get("nh3n", 0.0),
            timestamp=d.get("timestamp", 0),
        )

    def __str__(self):
        return (f"pH={self.ph:.2f}, TSS={self.tss:.2f}, "
                f"Debit={self.debit:.2f}, I={self.current:.2f}A, "
                f"V={self.voltage:.2f}V, t={self.timestamp}")

@dataclass
class SensorDataBuffer:
    """Buffer untuk menyimpan 30 data sensor sebelum dikirim"""
    data: List[SensorData] = field(default_factory=list)
    max_size: int = 30
    
    def add(self, sensor_data: SensorData) -> bool:
        """
        Tambahkan data sensor ke buffer
        Returns: True jika buffer penuh setelah ditambahkan
        """
        self.data.append(sensor_data)
        return len(self.data) >= self.max_size
    
    def is_full(self) -> bool:
        """Cek apakah buffer sudah penuh"""
        return len(self.data) >= self.max_size
    
    def clear(self):
        """Kosongkan buffer"""
        self.data.clear()
    
    def get_payload(self, uid: str, include_power: bool = False, device_id: str = None) -> dict:
        """Generate payload untuk API"""
        payload = {
            "uid": uid,
            "data": []
        }
        if device_id:
            payload["device_id"] = device_id
        for sensor in self.data:
            if include_power:
                payload["data"].append(sensor.to_dict_with_power())
            else:
                payload["data"].append(sensor.to_dict())
        return payload
    
    def save_cache(self, path: str = "buffer_cache.json"):
        """Persist isi buffer ke disk — dipanggil setiap selesai baca sensor.
        Melindungi data dari mati listrik / crash sebelum buffer penuh."""
        try:
            with open(path, 'w') as f:
                json.dump([s.to_raw_dict() for s in self.data], f)
        except Exception as e:
            print(f"[ERROR] Gagal menyimpan buffer cache: {e}")

    def load_cache(self, path: str = "buffer_cache.json") -> int:
        """Muat kembali buffer dari disk saat aplikasi start.
        Returns: jumlah data yang dipulihkan."""
        try:
            with open(path, 'r') as f:
                raw = json.load(f)
            self.data = [SensorData.from_raw_dict(d) for d in raw]
            return len(self.data)
        except FileNotFoundError:
            return 0
        except Exception as e:
            print(f"[ERROR] Gagal memuat buffer cache: {e}")
            return 0

    def __len__(self):
        return len(self.data)

@dataclass
class BackupData:
    """Data backup untuk disimpan ke file saat offline.

    Menyimpan payload MENTAH (bukan token JWT) — token dibuat baru saat
    kirim ulang dengan secret key terkini, sehingga rotasi key di server
    tidak membuat backup jadi invalid selamanya.
    `token` dipertahankan untuk kompatibilitas file backup format lama.
    """
    server_url: str
    timestamp: int
    payload: Optional[dict] = None   # payload mentah (uid + data)
    server_num: int = 0              # 1 = Mitra Mutiara, 2 = KLHK
    token: str = ""                  # format lama — dikirim apa adanya

    def to_dict(self) -> dict:
        return {
            "server_url": self.server_url,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "server_num": self.server_num,
            "token": self.token,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'BackupData':
        return cls(
            server_url=data["server_url"],
            timestamp=data["timestamp"],
            payload=data.get("payload"),
            server_num=data.get("server_num", 0),
            token=data.get("token", ""),
        )

class DataBackupManager:
    """Manager untuk backup data saat offline"""

    # ≈ 8 hari data (2 payload/jam) — cegah file membengkak tanpa batas
    # saat server mati berminggu-minggu; yang tertua dibuang lebih dulu
    MAX_ITEMS = 400

    def __init__(self, backup_file: str = "data_backup.json"):
        self.backup_file = backup_file
        self.backup_list: List[BackupData] = []
        self.load()

    def add(self, backup: BackupData):
        """Tambahkan data backup"""
        self.backup_list.append(backup)
        if len(self.backup_list) > self.MAX_ITEMS:
            dropped = len(self.backup_list) - self.MAX_ITEMS
            self.backup_list = self.backup_list[-self.MAX_ITEMS:]
            print(f"[WARN] Backup penuh ({self.MAX_ITEMS}) — {dropped} data tertua dibuang")
        self.save()
    
    def get_all(self) -> List[BackupData]:
        """Ambil semua data backup"""
        return self.backup_list.copy()
    
    def clear(self):
        """Hapus semua data backup"""
        self.backup_list.clear()
        self.save()
    
    def remove(self, backup: BackupData):
        """Hapus satu data backup"""
        if backup in self.backup_list:
            self.backup_list.remove(backup)
            self.save()
    
    def save(self):
        """Simpan ke file"""
        try:
            data = [b.to_dict() for b in self.backup_list]
            with open(self.backup_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[ERROR] Gagal menyimpan backup: {e}")
    
    def load(self):
        """Muat dari file"""
        try:
            with open(self.backup_file, 'r') as f:
                data = json.load(f)
                self.backup_list = [BackupData.from_dict(d) for d in data]
        except FileNotFoundError:
            self.backup_list = []
        except Exception as e:
            print(f"[ERROR] Gagal memuat backup: {e}")
            self.backup_list = []
    
    def has_pending_data(self) -> bool:
        """Cek apakah ada data yang belum terkirim"""
        return len(self.backup_list) > 0
    
    def __len__(self):
        return len(self.backup_list)
