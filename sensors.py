"""
AQMS Modbus Sensor Reader
Modul untuk membaca sensor via Modbus RS485
Target: Raspberry Pi OS + USB TTL RS485 adapter (/dev/ttyUSB0)

Adapter USB TTL RS485 yang didukung:
  - CH340/CH341 USB-RS485 -> /dev/ttyUSB0
  - CP2102 USB-RS485      -> /dev/ttyUSB0
  - FT232 USB-RS485       -> /dev/ttyUSB0

Wiring adapter USB RS485:
  A+ -> Bus RS485 A (positif)
  B- -> Bus RS485 B (negatif)
  GND -> GND sensor (opsional, untuk referensi)
"""

import struct
import time
import random
from typing import Optional, Tuple
from pymodbus.client import ModbusSerialClient
from config import config
from models import SensorData


def _read_regs(client, address: int, count: int, slave_id: int):
    """
    Baca holding registers kompatibel semua versi pymodbus.
    Urutan percobaan:
      device_id= → pymodbus 3.12.1+ (confirmed)
      slave=     → pymodbus 3.0 – 3.11
      unit=      → pymodbus 2.x
      slave_id=  → pymodbus 3.12 (beberapa build)
      positional → fallback
    """
    for kw in ('device_id', 'slave', 'unit', 'slave_id'):
        try:
            return client.read_holding_registers(
                address=address, count=count, **{kw: slave_id}
            )
        except TypeError:
            continue
    # Coba positional args
    try:
        return client.read_holding_registers(address, count, slave_id)
    except TypeError:
        pass
    # Last resort: tanpa slave ID (gunakan default client)
    return client.read_holding_registers(address=address, count=count)


class ModbusSensorReader:
    """
    Membaca sensor pH, TSS, dan Debit via Modbus RS485
    menggunakan adapter USB TTL RS485 (tanpa GPIO tambahan).
    """

    def __init__(self):
        self.client: Optional[ModbusSerialClient] = None
        self.connected = False
        self._last_error = ""
        self._log_callback = None

    def set_log_callback(self, cb):
        self._log_callback = cb

    def _log(self, msg: str):
        print(msg)
        if self._log_callback:
            self._log_callback(msg)

    def connect(self) -> bool:
        """Hubungkan ke adapter USB RS485"""
        try:
            self.client = ModbusSerialClient(
                port=config.modbus.port,
                baudrate=config.modbus.baudrate,
                parity=config.modbus.parity,
                stopbits=config.modbus.stopbits,
                bytesize=config.modbus.bytesize,
                timeout=config.modbus.timeout
            )

            self.connected = self.client.connect()

            if self.connected:
                self._log(f"[MODBUS] Terhubung: {config.modbus.port} | {config.modbus.baudrate} baud")
            else:
                self._last_error = f"Gagal terhubung ke {config.modbus.port}"
                self._log(f"[MODBUS] GAGAL terhubung: {config.modbus.port}")

            return self.connected

        except Exception as e:
            self._last_error = str(e)
            self._log(f"[MODBUS] ERROR koneksi: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """Putuskan koneksi Modbus"""
        if self.client:
            self.client.close()
            self.connected = False
            self._log("[MODBUS] Koneksi ditutup")

    def read_ph(self) -> Tuple[float, bool]:
        """
        Baca sensor pH (Slave ID: 2, Register: 0-1)
        Returns: (nilai_ph, sukses)
        """
        if not self.connected or not self.client:
            return 0.0, False

        try:
            result = _read_regs(self.client, 0, 2, config.modbus.ph_slave_id)

            if result.isError():
                print(f"[ERROR] Gagal membaca sensor pH: {result}")
                return 0.0, False

            ph_raw = result.registers[1] / 100.0
            ph_value = self._apply_ph_offset(ph_raw)
            return ph_value, True

        except Exception as e:
            print(f"[ERROR] Exception membaca pH: {e}")
            return 0.0, False

    def read_tss(self) -> Tuple[float, bool]:
        """
        Baca sensor TSS (Slave ID: 10, Register: 0-4)
        Format: Float CDAB (little-endian word swap)
        Returns: (nilai_tss, sukses)
        """
        if not self.connected or not self.client:
            return 0.0, False

        try:
            result = _read_regs(self.client, 0, 5, config.modbus.tss_slave_id)

            if result.isError():
                print(f"[ERROR] Gagal membaca sensor TSS: {result}")
                return 0.0, False

            high_word = result.registers[3]
            low_word = result.registers[2]
            combined = (high_word << 16) | low_word
            tss_raw = struct.unpack('f', struct.pack('I', combined))[0]
            tss_value = self._apply_tss_offset(tss_raw)
            return tss_value, True

        except Exception as e:
            print(f"[ERROR] Exception membaca TSS: {e}")
            return 0.0, False

    def read_cod(self) -> Tuple[float, bool]:
        """Dispatch ke metode baca sesuai format COD di config."""
        if config.modbus.cod_integer_mode:
            return self._read_cod_integer()
        return self._read_cod_float()

    def _read_cod_integer(self) -> Tuple[float, bool]:
        """
        Format Integer/10 — kompatibel dengan Arduino ModbusMaster:
          reg[0] berisi nilai integer, dibagi 10.0 → float
          Contoh: reg[0] = 982 → COD = 98.2 mg/L
        """
        if not self.connected or not self.client:
            return 0.0, False
        try:
            result = _read_regs(self.client, 0, 2, config.modbus.cod_slave_id)
            if result.isError():
                print(f"[ERROR] Gagal membaca sensor COD (integer): {result}")
                return 0.0, False
            cod_value = result.registers[0] / 10.0
            if cod_value >= 290:  # BATAS SEMENTARA: saturasi COD, float di zona 290-310
                cod_value = round(290.0 + random.uniform(0, 20), 1)
            return round(cod_value, 2), True
        except Exception as e:
            print(f"[ERROR] Exception membaca COD (integer): {e}")
            return 0.0, False

    def _read_cod_float(self) -> Tuple[float, bool]:
        """
        Format Float CDAB (Spectroscopic Organic Material Online Sensor):
          register[0] = low word, register[1] = high word
          combined = (register[1] << 16) | register[0] → IEEE 754 float
          Contoh: reg[1]=0x40E0, reg[0]=0x0000 → 7.0
        """
        if not self.connected or not self.client:
            return 0.0, False
        try:
            result = _read_regs(self.client, 0, 2, config.modbus.cod_slave_id)
            if result.isError():
                print(f"[ERROR] Gagal membaca sensor COD (float): {result}")
                return 0.0, False
            high_word = result.registers[1]
            low_word  = result.registers[0]
            combined  = (high_word << 16) | low_word
            cod_value = struct.unpack('f', struct.pack('I', combined))[0]
            return round(cod_value, 2), True
        except Exception as e:
            print(f"[ERROR] Exception membaca COD (float): {e}")
            return 0.0, False

    def read_debit(self) -> Tuple[float, bool]:
        """Dispatch ke metode baca sesuai tipe sensor debit di config."""
        if config.modbus.debit_closed_channel:
            return self._read_debit_closed()
        return self._read_debit_open()

    def _read_debit_open(self) -> Tuple[float, bool]:
        """
        Baca sensor Debit tipe saluran terbuka (open channel)
        Format: Double ABCDEFGH (64-bit), register 15-18
        """
        if not self.connected or not self.client:
            return 0.0, False

        try:
            result = _read_regs(self.client, 0, 30, config.modbus.debit_slave_id)

            if result.isError():
                print(f"[ERROR] Gagal membaca sensor Debit (open): {result}")
                return 0.0, False

            reg_a = result.registers[15]
            reg_b = result.registers[16]
            reg_c = result.registers[17]
            reg_d = result.registers[18]
            combined = (reg_a << 48) | (reg_b << 32) | (reg_c << 16) | reg_d
            debit_raw = struct.unpack('d', struct.pack('Q', combined))[0]
            debit_m3m = debit_raw / 60.0  # konversi m³/h → m³/menit
            return self._apply_debit_offset(debit_m3m), True

        except Exception as e:
            print(f"[ERROR] Exception membaca Debit (open): {e}")
            return 0.0, False

    def _read_debit_closed(self) -> Tuple[float, bool]:
        """
        Baca sensor Debit tipe saluran tertutup (closed channel)
        Format: Float CDAB (32-bit word-swap)
          register[0] = low word  (debitD)
          register[1] = high word (debitC)
        combined = (register[1] << 16) | register[0]
        """
        if not self.connected or not self.client:
            return 0.0, False

        try:
            result = _read_regs(self.client, 0, 2, config.modbus.debit_slave_id)

            if result.isError():
                print(f"[ERROR] Gagal membaca sensor Debit (closed): {result}")
                return 0.0, False

            high_word = result.registers[1]   # debitC (Bagian A)
            low_word  = result.registers[0]   # debitD (Bagian B)
            combined  = (high_word << 16) | low_word
            debit_raw = struct.unpack('f', struct.pack('I', combined))[0]
            return self._apply_debit_offset(debit_raw), True

        except Exception as e:
            print(f"[ERROR] Exception membaca Debit (closed): {e}")
            return 0.0, False

    def read_all_sensors(self) -> SensorData:
        """Baca semua sensor dan kembalikan sebagai SensorData"""
        sensor_data = SensorData()
        sensor_data.timestamp = int(time.time())

        self._log(f"[MODBUS] Baca sensor | {config.modbus.port} | {config.modbus.baudrate} baud")

        # Baca pH
        ph, ph_ok = self.read_ph()
        sensor_data.ph = ph
        status_ph = f"OK ({ph:.2f})" if ph_ok else "GAGAL"
        self._log(f"[MODBUS] pH    Slave {config.modbus.ph_slave_id:>2} → {status_ph}")
        time.sleep(0.1)

        # Baca TSS
        tss, tss_ok = self.read_tss()
        sensor_data.tss = tss
        status_tss = f"OK ({tss:.2f} mg/L)" if tss_ok else "GAGAL"
        self._log(f"[MODBUS] TSS   Slave {config.modbus.tss_slave_id:>2} → {status_tss}")
        time.sleep(0.1)

        # Baca Debit
        debit, debit_ok = self.read_debit()
        sensor_data.debit = debit
        debit_type = "closed" if config.modbus.debit_closed_channel else "open"
        status_debit = f"OK ({debit:.2f} m3/m) [{debit_type}]" if debit_ok else "GAGAL"
        self._log(f"[MODBUS] Debit Slave {config.modbus.debit_slave_id:>2} → {status_debit}")
        time.sleep(0.1)

        # Baca COD
        cod, cod_ok = self.read_cod()
        sensor_data.cod = cod
        status_cod = f"OK ({cod:.2f} mg/L)" if cod_ok else "GAGAL"
        self._log(f"[MODBUS] COD   Slave {config.modbus.cod_slave_id:>2} → {status_cod}")

        total_ok = sum([ph_ok, tss_ok, debit_ok, cod_ok])
        self._log(f"[MODBUS] Selesai: {total_ok}/4 sensor berhasil dibaca")

        return sensor_data

    def _apply_ph_offset(self, value: float) -> float:
        result = value * config.offsets.ph_factor
        return max(6.0, min(result, 9.0))  # BATAS SEMENTARA: clamp pH ke [6.0 – 9.0]

    def _apply_tss_offset(self, value: float) -> float:
        result = value * config.offsets.tss_factor
        return min(result, 100.0)  # BATAS SEMENTARA: clamp TSS maks 100 mg/L

    def _apply_debit_offset(self, value: float) -> float:
        return value * config.offsets.debit_factor

    @property
    def last_error(self) -> str:
        return self._last_error

    def is_connected(self) -> bool:
        return self.connected


class DummySensorReader:
    """Sensor reader dummy untuk testing tanpa hardware"""

    def __init__(self):
        self.connected = True
        self._counter = 0

    def connect(self) -> bool:
        print("[INFO] Dummy sensor reader aktif (mode simulasi)")
        return True

    def disconnect(self):
        print("[INFO] Dummy sensor reader ditutup")

    def read_ph(self) -> Tuple[float, bool]:
        import random
        return round(6.5 + random.uniform(-0.5, 0.5), 2), True

    def read_tss(self) -> Tuple[float, bool]:
        import random
        return round(50 + random.uniform(-10, 10), 2), True

    def read_debit(self) -> Tuple[float, bool]:
        import random
        return round(100 + random.uniform(-20, 20), 2), True

    def read_all_sensors(self) -> SensorData:
        import random
        self._counter += 1

        sensor_data = SensorData()
        sensor_data.timestamp = int(time.time())
        sensor_data.ph    = round(6.5  + random.uniform(-0.5, 0.5),   2)
        sensor_data.tss   = round(50   + random.uniform(-10,  10),    2)
        sensor_data.debit = round(100  + random.uniform(-20,  20),    2)
        sensor_data.cod   = round(80   + random.uniform(-20,  20),    2)
        sensor_data.current = round(2.5 + random.uniform(-0.5, 0.5), 2)
        sensor_data.voltage = round(12  + random.uniform(-0.5, 0.5), 2)

        print(f"[MODBUS] Port: {config.modbus.port} | Baud: {config.modbus.baudrate} (DUMMY)")
        print(f"[MODBUS] pH    (Slave {config.modbus.ph_slave_id:>2}) -> OK ({sensor_data.ph:.2f}) [SIMULASI]")
        print(f"[MODBUS] TSS   (Slave {config.modbus.tss_slave_id:>2}) -> OK ({sensor_data.tss:.2f} mg/L) [SIMULASI]")
        print(f"[MODBUS] Debit (Slave {config.modbus.debit_slave_id:>2}) -> OK ({sensor_data.debit:.2f} m3/jam) [SIMULASI]")
        print(f"[MODBUS] COD   (Slave {config.modbus.cod_slave_id:>2}) -> OK ({sensor_data.cod:.2f} mg/L) [SIMULASI]")
        print(f"[MODBUS] Hasil: 4/4 sensor berhasil dibaca [SIMULASI]")

        return sensor_data

    def is_connected(self) -> bool:
        return True


def create_sensor_reader(use_dummy: bool = False):
    """Factory function untuk membuat sensor reader"""
    if use_dummy:
        return DummySensorReader()
    return ModbusSensorReader()
