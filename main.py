"""
AQMS Monitoring System - Main Application
Sistem monitoring kualitas air untuk Raspberry Pi 4

Fitur:
- Pembacaan sensor pH, TSS, Debit via Modbus RS485 (GPIO)
- Kontrol arah RS485 via GPIO pin (DE/RE pada MAX485)
- Pengiriman data ke 2 server API dengan JWT
- Tampilan GUI modern untuk display HDMI
- Backup data offline saat tidak ada koneksi
- Monitoring suhu CPU dan resource Raspberry Pi

Author: AQMS Team
Target: Raspberry Pi 4 + Modul RS485 (MAX485) via GPIO
"""

import sys
import time
import threading
import argparse
from datetime import datetime
from typing import Optional

from config import config
from models import SensorData, SensorDataBuffer, OperationalState, OperationalStatus
from sensors import create_sensor_reader, ModbusSensorReader, DummySensorReader
from api_client import APIClient
from history import SensorHistory
from anomaly import AnomalyDetector
from gui import create_application, MainWindow, SignalBridge

# ============================================================
# WORKER THREAD
# ============================================================

class AQMSWorker:
    """
    Worker thread untuk pembacaan sensor dan pengiriman data
    Berjalan di background tanpa memblokir GUI
    """

    def __init__(self, signal_bridge: SignalBridge, use_dummy_sensor: bool = False):
        self.signal_bridge = signal_bridge
        self.use_dummy_sensor = use_dummy_sensor

        self.sensor_reader = create_sensor_reader(use_dummy_sensor)
        if hasattr(self.sensor_reader, 'set_log_callback'):
            self.sensor_reader.set_log_callback(
                lambda msg: signal_bridge.modbus_log.emit(msg)
            )
        self.api_client = APIClient()
        self.api_client.set_log_callback(
            lambda msg: signal_bridge.log_entry.emit(msg)
        )
        self.data_buffer = SensorDataBuffer()

        self.running = False
        self.thread: Optional[threading.Thread] = None

        self._last_sensor_read = 0
        self._last_connection_check = 0
        self._last_backup_retry = 0
        self._secret_key_fetched = False
        self._internet_connected = None  # None = belum pernah dicek
        self._consec_fail = 0  # pembacaan gagal total beruntun → trigger reconnect
        self.daily_sent_count = 0
        self._daily_date = datetime.now().date()
        self.history = SensorHistory()  # riwayat pembacaan ke SQLite lokal
        self.anomaly = AnomalyDetector()  # deteksi lonjakan / sensor macet

        # Pulihkan buffer dari disk (data sebelum mati listrik / restart)
        restored = self.data_buffer.load_cache()
        if restored:
            print(f"[INFO] {restored} data dipulihkan dari buffer cache")

    def start(self):
        """Mulai worker thread"""
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        print("[INFO] Worker thread dimulai")

    def stop(self):
        """Hentikan worker thread dan bersihkan resource"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

        # Bersihkan koneksi sensor (termasuk GPIO cleanup)
        self.sensor_reader.disconnect()
        print("[INFO] Worker thread dihentikan")

    def _wait_for_valid_clock(self):
        """Tunggu jam sistem valid sebelum membaca sensor.

        Raspberry Pi tidak punya RTC — boot tanpa internet membuat jam
        salah (misal 1970) sehingga semua timestamp data invalid di server.
        Tunggu sampai NTP sync (tahun masuk akal) sebelum lanjut.
        """
        warned = False
        while self.running:
            if datetime.now().year >= 2025:
                if warned:
                    print("[INFO] Jam sistem sudah valid, melanjutkan")
                    self.signal_bridge.notification.emit("Jam tersinkronisasi", 3000)
                return
            if not warned:
                print("[WARN] Jam sistem belum valid — menunggu sinkronisasi NTP...")
                self.signal_bridge.notification.emit(
                    "Menunggu sinkronisasi jam (NTP)...", 5000)
                warned = True
            time.sleep(10)

    def _run(self):
        """Main loop worker"""
        self._init_sensors()
        self._init_connection()
        self._wait_for_valid_clock()

        # Tampilkan jumlah buffer yang dipulihkan di GUI
        if len(self.data_buffer):
            self.signal_bridge.data_count_update.emit(
                len(self.data_buffer), config.timing.data_send_count
            )
            self.signal_bridge.notification.emit(
                f"{len(self.data_buffer)} data dipulihkan dari cache", 4000
            )

        # Tampilkan backup pending dari sesi sebelumnya
        self.signal_bridge.backup_count_update.emit(self.api_client.pending_backup_count)

        while self.running:
            current_time = time.time()

            # Cek koneksi internet setiap interval (default 60 detik)
            if current_time - self._last_connection_check >= config.network.connection_check_interval:
                self._check_connection()
                self._last_connection_check = current_time

            # Ambil secret key jika belum
            if self._internet_connected and not self._secret_key_fetched:
                self._fetch_secret_keys()

            # Retry backup tiap interval (default 5 menit) saat online —
            # tidak menunggu siklus kirim per jam
            if (self._internet_connected
                    and self.api_client.pending_backup_count > 0
                    and current_time - self._last_backup_retry >= config.timing.backup_retry_interval):
                self.api_client.retry_backup()
                self._last_backup_retry = current_time
                self.signal_bridge.backup_count_update.emit(self.api_client.pending_backup_count)

            # Baca sensor setiap interval (default 2 menit)
            if current_time - self._last_sensor_read >= config.timing.sensor_read_interval:
                self._read_sensors()
                self._last_sensor_read = current_time

            # Sleep untuk mengurangi CPU usage
            time.sleep(0.5)

    def _init_sensors(self):
        """Inisialisasi koneksi sensor"""
        print("[INFO] Menginisialisasi sensor...")
        if self.sensor_reader.connect():
            self.signal_bridge.notification.emit("Sensor terhubung", 3000)
            # Update GPIO status di GUI
            if hasattr(self.sensor_reader, 'gpio_available'):
                gpio_ok = self.sensor_reader.gpio_available
                print(f"[INFO] GPIO RS485 status: {'Active' if gpio_ok else 'Not Available'}")
        else:
            self.signal_bridge.notification.emit("Gagal koneksi sensor", 5000)

    def _init_connection(self):
        """Inisialisasi koneksi internet"""
        print("[INFO] Mengecek koneksi internet...")
        self._check_connection()

    def _check_connection(self):
        """Cek status koneksi internet"""
        connected = self.api_client.check_internet_connection()

        if connected != self._internet_connected:
            self._internet_connected = connected
            self.signal_bridge.connection_update.emit(connected)

            status = "Terhubung" if connected else "Terputus"
            print(f"[INFO] Status internet: {status}")

    def _fetch_secret_keys(self):
        """Ambil secret key dari server"""
        print("[INFO] Mengambil secret key...")
        if self.api_client.fetch_all_secret_keys():
            self._secret_key_fetched = True
            self.signal_bridge.notification.emit("Secret key berhasil diambil", 3000)
            k1 = self.api_client.secret_key_1
            k2 = self.api_client.secret_key_2
            p1 = (k1[:8] + "...") if len(k1) > 8 else k1
            p2 = (k2[:8] + "...") if len(k2) > 8 else k2
            self.signal_bridge.secret_key_update.emit(p1, p2)
        else:
            self.signal_bridge.notification.emit("Gagal ambil secret key", 3000)

    def _read_sensors(self):
        """Baca data dari semua sensor"""
        count = len(self.data_buffer) + 1
        print("")
        print(f"{'='*50}")
        print(f"[INFO] Pembacaan sensor #{count}/{config.timing.data_send_count}")
        print(f"{'='*50}")

        # Koneksi belum/tidak terjalin → coba sambungkan dulu
        if not self.sensor_reader.is_connected() and hasattr(self.sensor_reader, 'reconnect'):
            self.sensor_reader.reconnect()

        # Baca sensor
        sensor_data = self.sensor_reader.read_all_sensors()

        # Deteksi kegagalan total beruntun → reconnect Modbus
        # (menangani USB adapter lepas-pasang tanpa perlu restart aplikasi)
        ok_count = getattr(self.sensor_reader, 'last_ok_count', -1)
        if ok_count == 0:
            self._consec_fail += 1
            if self._consec_fail >= 2 and hasattr(self.sensor_reader, 'reconnect'):
                print(f"[WARN] {self._consec_fail}x pembacaan gagal total — reconnect Modbus")
                if self.sensor_reader.reconnect():
                    self.signal_bridge.notification.emit("Modbus tersambung kembali", 3000)
                self._consec_fail = 0
        elif ok_count > 0:
            self._consec_fail = 0

        # Update GUI
        self.signal_bridge.sensor_update.emit(sensor_data)

        # Tambah ke buffer + persist ke disk (proteksi mati listrik)
        buffer_full = self.data_buffer.add(sensor_data)
        self.data_buffer.save_cache()
        self.history.insert(sensor_data)  # riwayat permanen (SQLite)

        # Deteksi anomali — peringatan saja, data tidak diubah
        if config.modbus.anomaly_enabled:
            for msg in self.anomaly.check_all(sensor_data):
                print(f"[ANOMALI] {msg}")
                self.signal_bridge.modbus_log.emit(f"[ANOMALI] {msg}")
        self.signal_bridge.data_count_update.emit(
            len(self.data_buffer),
            config.timing.data_send_count
        )

        # Log ringkasan
        print(f"[DATA] pH={sensor_data.ph:.2f} | TSS={sensor_data.tss:.2f} | Debit={sensor_data.debit:.2f} | V={sensor_data.voltage:.1f} | I={sensor_data.current:.1f}")
        print(f"[BUFFER] {len(self.data_buffer)}/{config.timing.data_send_count} {'(KIRIM!)' if buffer_full else ''}")
        print(f"{'='*50}")

        # Kirim jika buffer penuh
        if buffer_full:
            self._send_data()

    def _send_data(self):
        """Kirim data ke server"""
        print(f"[INFO] Mengirim {len(self.data_buffer)} data ke server...")

        # Reset counter harian saat ganti hari
        today = datetime.now().date()
        if today != self._daily_date:
            self._daily_date = today
            self.daily_sent_count = 0

        success1, success2 = self.api_client.send_all_data(self.data_buffer)

        # Update status per server + backup pending di sidebar GUI
        self.signal_bridge.server_status_update.emit(success1, success2)
        self.signal_bridge.backup_count_update.emit(self.api_client.pending_backup_count)

        # Update daily count sebelum clear buffer
        sent_count = len(self.data_buffer)

        # Clear buffer + kosongkan cache disk
        # (data sudah terkirim atau sudah aman di data_backup.json)
        self.data_buffer.clear()
        self.data_buffer.save_cache()
        self.signal_bridge.data_count_update.emit(0, config.timing.data_send_count)

        # Notifikasi
        if success1 and success2:
            self.signal_bridge.notification.emit("Data berhasil dikirim", 3000)
            self.daily_sent_count += sent_count
            self.signal_bridge.daily_data_update.emit(self.daily_sent_count)
        elif success1 or success2:
            self.signal_bridge.notification.emit("Data terkirim sebagian", 3000)
            self.daily_sent_count += sent_count
            self.signal_bridge.daily_data_update.emit(self.daily_sent_count)
        else:
            self.signal_bridge.notification.emit("Gagal kirim, disimpan ke backup", 3000)

        # Update secret key
        if self._internet_connected:
            self.api_client.check_and_update_secret_keys()


# ============================================================
# MAIN APPLICATION
# ============================================================

def main():
    """Entry point aplikasi"""
    parser = argparse.ArgumentParser(description='AQMS Monitoring System - Raspberry Pi 4')
    parser.add_argument(
        '--dummy', '-d',
        action='store_true',
        help='Gunakan sensor dummy untuk testing'
    )
    parser.add_argument(
        '--windowed', '-w',
        action='store_true',
        help='Jalankan dalam mode jendela (default: fullscreen)'
    )
    parser.add_argument(
        '--interval', '-i',
        type=int,
        default=None,
        help='Interval pembacaan sensor dalam detik (default: 120)'
    )
    args = parser.parse_args()

    # Load konfigurasi
    config.load()

    # Pulihkan status operasional terakhir (-1/-2/-3 tetap aktif setelah restart)
    OperationalState.load()

    # Override interval jika diberikan
    if args.interval:
        config.timing.sensor_read_interval = args.interval
        print(f"[INFO] Interval sensor diubah ke {args.interval} detik")

    print("=" * 60)
    print("  AQMS MONITORING SYSTEM")
    print("  Air Quality Monitoring - Raspberry Pi 4")
    print("=" * 60)
    print(f"  Mode       : {'Dummy Sensor' if args.dummy else 'Real Sensor (USB RS485)'}")
    print(f"  Port       : {config.modbus.port}")
    print(f"  Baudrate   : {config.modbus.baudrate}")
    print(f"  Interval   : {config.timing.sensor_read_interval} detik")
    print(f"  Server 1   : {config.server.server_url_1}")
    print(f"  Server 2   : {config.server.server_url_2}")
    print("=" * 60)

    # Buat aplikasi GUI
    app, window = create_application()

    # Buat worker
    worker = AQMSWorker(
        signal_bridge=window.signal_bridge,
        use_dummy_sensor=args.dummy
    )

    # Update GPIO status di GUI setelah worker siap
    if hasattr(worker.sensor_reader, 'gpio_available'):
        window.update_gpio_status(worker.sensor_reader.gpio_available)

    # Tampilkan status operasional yang dipulihkan di GUI (tanpa dialog)
    if not OperationalState.is_normal():
        window._set_status(OperationalState.get(), confirm=False)

    # Tampilkan window (default fullscreen, --windowed untuk mode jendela)
    if args.windowed:
        window.showMaximized()
        window._fs_btn.setText("LAYAR PENUH")
    else:
        window.showFullScreen()

    # Mulai worker
    worker.start()

    # Run aplikasi
    try:
        exit_code = app.exec()
    finally:
        worker.stop()
        config.save()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
