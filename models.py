"""
AQMS Data Models
Model data untuk sensor dan pengiriman
"""

from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
import json

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
    
    def to_dict(self) -> dict:
        """Konversi ke dictionary untuk JSON"""
        return {
            "datetime": self.timestamp,
            "pH": round(self.ph, 2),
            "tss": round(self.tss, 2),
            "debit": round(self.debit, 2),
            "cod": round(self.cod, 2),
            "nh3n": round(self.nh3n, 2)
        }
    
    def to_dict_with_power(self) -> dict:
        """Konversi ke dictionary dengan data arus dan tegangan"""
        return {
            "datetime": self.timestamp,
            "pH": round(self.ph, 2),
            "tss": round(self.tss, 2),
            "debit": round(self.debit, 2),
            "current": round(self.current, 2),
            "voltage": round(self.voltage, 2),
            "cod": round(self.cod, 2),
            "nh3n": round(self.nh3n, 2)
        }
    
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
    
    def __len__(self):
        return len(self.data)

@dataclass
class BackupData:
    """Data backup untuk disimpan ke file saat offline"""
    token: str
    server_url: str
    timestamp: int
    
    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "server_url": self.server_url,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'BackupData':
        return cls(
            token=data["token"],
            server_url=data["server_url"],
            timestamp=data["timestamp"]
        )

class DataBackupManager:
    """Manager untuk backup data saat offline"""
    
    def __init__(self, backup_file: str = "data_backup.json"):
        self.backup_file = backup_file
        self.backup_list: List[BackupData] = []
        self.load()
    
    def add(self, backup: BackupData):
        """Tambahkan data backup"""
        self.backup_list.append(backup)
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
