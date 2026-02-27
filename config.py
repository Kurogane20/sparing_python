"""
AQMS Configuration Module
Konfigurasi untuk sistem monitoring kualitas air
Target: Raspberry Pi OS + USB TTL RS485 (/dev/ttyUSB0)
"""

import os
import json
import platform
from dataclasses import dataclass, asdict
from typing import Optional
from pathlib import Path

CONFIG_FILE = Path("config.json")

@dataclass
class ServerConfig:
    """Konfigurasi server API"""
    # Server 1 - Mitra Mutiara
    server_url_1: str = "https://sparingapi.mitramutiara.co.id/api/post-data"
    secret_key_url_1: str = "https://sparingapi.mitramutiara.co.id/api/get-key"
    uid_1: str = "admin-LOG"
    device_id_1: str = "DEVICE-001"
    
    # Server 2 - KLHK
    server_url_2: str = "https://sparing.kemenlh.go.id/api/send-hourly"
    secret_key_url_2: str = "https://sparing.kemenlh.go.id/api/secret-sensor"
    uid_2: str = "tesuid2"

def _default_serial_port() -> str:
    """Deteksi port serial default berdasarkan platform"""
    if platform.system() == "Linux":
        return "/dev/ttyUSB0"  # USB TTL RS485 adapter di Raspberry Pi OS
    return "COM3"  # Windows fallback

@dataclass
class ModbusConfig:
    """Konfigurasi Modbus RS485 via USB TTL (Raspberry Pi OS)"""
    port: str = ""  # Diisi otomatis oleh __post_init__
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8
    timeout: float = 1.0

    # Slave IDs
    ph_slave_id: int = 2
    tss_slave_id: int = 10
    debit_slave_id: int = 1

    def __post_init__(self):
        if not self.port:
            self.port = _default_serial_port()

@dataclass
class SensorOffsets:
    """Offset kalibrasi sensor"""
    ph_offset: float = 0.0
    tss_offset: float = 0.0
    debit_offset: float = 0.0

@dataclass
class NetworkConfig:
    """Konfigurasi jaringan"""
    ntp_server: str = "pool.ntp.org"
    connection_check_url: str = "http://www.google.com"
    connection_check_interval: int = 5  # detik
    wifi_ssid: str = ""
    wifi_password: str = ""

@dataclass
class TimingConfig:
    """Konfigurasi waktu"""
    sensor_read_interval: int = 120  # 2 menit dalam detik
    data_send_count: int = 30  # Jumlah data sebelum kirim
    wifi_check_interval: int = 1  # detik

import dataclasses as _dc

def _safe_load(cls, data: dict):
    """Buat dataclass dari dict, abaikan key yang tidak dikenal."""
    valid = {f.name for f in _dc.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in valid})

@dataclass
class AppConfig:
    """Konfigurasi utama aplikasi"""
    server: ServerConfig
    modbus: ModbusConfig
    offsets: SensorOffsets
    network: NetworkConfig
    timing: TimingConfig
    data_backup_file: str = "data_backup.json"
    
    def __init__(self):
        self.server = ServerConfig()
        self.modbus = ModbusConfig()
        self.offsets = SensorOffsets()
        self.network = NetworkConfig()
        self.timing = TimingConfig()
    
    def save(self):
        """Simpan konfigurasi ke file JSON"""
        config_dict = {
            "server": asdict(self.server),
            "modbus": asdict(self.modbus),
            "offsets": asdict(self.offsets),
            "network": asdict(self.network),
            "timing": asdict(self.timing),
            "data_backup_file": self.data_backup_file
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config_dict, f, indent=4)
        print(f"[INFO] Konfigurasi disimpan ke {CONFIG_FILE}")
    
    def load(self):
        """Muat konfigurasi dari file JSON"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config_dict = json.load(f)
                
                if "server" in config_dict:
                    self.server = _safe_load(ServerConfig, config_dict["server"])
                if "modbus" in config_dict:
                    self.modbus = _safe_load(ModbusConfig, config_dict["modbus"])
                if "offsets" in config_dict:
                    self.offsets = _safe_load(SensorOffsets, config_dict["offsets"])
                if "network" in config_dict:
                    self.network = _safe_load(NetworkConfig, config_dict["network"])
                if "timing" in config_dict:
                    self.timing = _safe_load(TimingConfig, config_dict["timing"])
                if "data_backup_file" in config_dict:
                    self.data_backup_file = config_dict["data_backup_file"]
                    
                print(f"[INFO] Konfigurasi dimuat dari {CONFIG_FILE}")
            except Exception as e:
                print(f"[ERROR] Gagal memuat konfigurasi: {e}")
                self.save()  # Simpan default jika gagal
        else:
            print("[INFO] File konfigurasi tidak ditemukan, menggunakan default")
            self.save()

# Singleton instance
config = AppConfig()
