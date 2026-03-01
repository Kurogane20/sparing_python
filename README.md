# AQMS Monitoring System

Sistem monitoring kualitas air (Air Quality Monitoring System) untuk Mini PC Windows dengan display HDMI. Aplikasi ini merupakan konversi dari kode ESP32 Arduino ke Python.

## Fitur

- **Pembacaan Sensor via Modbus RS485**
  - Sensor pH (Slave ID: 2)
  - Sensor TSS (Slave ID: 10)
  - Sensor Debit/Flow (Slave ID: 1)
  - Sensor Arus dan Tegangan (simulasi)

- **Komunikasi API**
  - Pengiriman data ke 2 server (Mitra Mutiara & KLHK)
  - Autentikasi JWT dengan HS256
  - Backup data offline saat tidak ada koneksi

- **Tampilan GUI Modern**
  - Dashboard monitoring real-time
  - Status koneksi internet
  - Halaman pengaturan
  - Notifikasi sistem

- **Konfigurasi Fleksibel**
  - Offset kalibrasi sensor
  - Port RS485
  - UID dan endpoint server

## Persyaratan Sistem

- Windows 10/11
- Python 3.10 atau lebih baru
- USB to RS485 Converter
- Display HDMI

## Instalasi

### 1. Install Python

Download dan install Python dari [python.org](https://python.org). Pastikan mencentang "Add Python to PATH" saat instalasi.

### 2. Clone/Download Project

Download semua file ke folder, misalnya `C:\AQMS`

### 3. Install Dependencies

Buka Command Prompt di folder project dan jalankan:

```batch
pip install -r requirements.txt
```

Atau gunakan virtual environment:

```batch
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Penggunaan

### Mode Normal (dengan sensor)

Double-click `run.bat` atau jalankan:

```batch
python main.py
```

### Mode Dummy (testing tanpa sensor)

Double-click `run_dummy.bat` atau jalankan:

```batch
python main.py --dummy
```

### Opsi Command Line

```
python main.py [OPTIONS]

Options:
  -d, --dummy       Gunakan sensor dummy untuk testing
  -f, --fullscreen  Jalankan dalam mode fullscreen
  -i, --interval N  Interval pembacaan sensor dalam detik (default: 120)
```

Contoh:

```batch
# Mode dummy dengan interval 10 detik dan fullscreen
python main.py --dummy --interval 10 --fullscreen
```

## Konfigurasi

Konfigurasi disimpan di `config.json`. Dapat diubah melalui GUI atau edit langsung:

```json
{
    "server": {
        "server_url_1": "https://sparing.mitramutiara.co.id/api/post-data",
        "secret_key_url_1": "https://sparing.mitramutiara.co.id/api/get-key",
        "uid_1": "PKN-LOG",
        "server_url_2": "https://sparing.kemenlh.go.id/api/send-hourly",
        "secret_key_url_2": "https://sparing.kemenlh.go.id/api/secret-sensor",
        "uid_2": "tesuid2"
    },
    "modbus": {
        "port": "COM3",
        "baudrate": 9600,
        "ph_slave_id": 2,
        "tss_slave_id": 10,
        "debit_slave_id": 1
    },
    "offsets": {
        "ph_offset": 0.0,
        "tss_offset": 0.0,
        "debit_offset": 0.0
    },
    "timing": {
        "sensor_read_interval": 120,
        "data_send_count": 30
    }
}
```

## Struktur File

```
aqms-python/
├── main.py           # Entry point aplikasi
├── config.py         # Modul konfigurasi
├── models.py         # Model data
├── sensors.py        # Pembacaan sensor Modbus
├── api_client.py     # Komunikasi API & JWT
├── gui.py            # Tampilan GUI PyQt6
├── requirements.txt  # Dependencies
├── run.bat           # Script startup Windows
├── run_dummy.bat     # Script testing dummy
├── config.json       # File konfigurasi (auto-generated)
└── data_backup.json  # Backup data offline (auto-generated)
```

## Koneksi Hardware

### USB to RS485

| USB-RS485 | Sensor |
|-----------|--------|
| A+ (D+)   | A+     |
| B- (D-)   | B-     |
| GND       | GND    |

### Port COM

Cek port COM di Device Manager Windows. Ubah di `config.json` atau GUI sesuai port yang tersedia.

## Troubleshooting

### "Python tidak ditemukan"
- Install Python dari python.org
- Pastikan Python ditambahkan ke PATH

### "Gagal koneksi sensor"
- Cek koneksi kabel RS485
- Pastikan port COM benar di konfigurasi
- Cek slave ID sensor

### "Gagal kirim data"
- Cek koneksi internet
- Data akan otomatis disimpan ke backup dan dikirim ulang

### GUI tidak muncul
- Install ulang PyQt6: `pip install --force-reinstall PyQt6`

## Alur Kerja Sistem

1. **Inisialisasi**
   - Load konfigurasi dari `config.json`
   - Koneksi ke sensor via RS485
   - Cek koneksi internet
   - Ambil secret key dari server

2. **Loop Utama**
   - Baca sensor setiap 2 menit (120 detik)
   - Simpan data ke buffer (max 30 data)
   - Update tampilan GUI

3. **Pengiriman Data**
   - Setelah 30 data terkumpul (1 jam)
   - Buat JWT token untuk masing-masing server
   - Kirim ke server 1 (Mitra Mutiara) dan server 2 (KLHK)
   - Jika gagal, simpan ke backup file

4. **Recovery**
   - Saat koneksi pulih, kirim data backup terlebih dahulu
   - Perbarui secret key jika berubah

## Perbedaan dengan Versi ESP32

| Fitur | ESP32 | Python |
|-------|-------|--------|
| Display | Nextion Serial | PyQt6 HDMI |
| Storage | SD Card | JSON File |
| WiFi | ESP32 WiFi | Ethernet/WiFi Windows |
| Config | SD Card txt | JSON config |
| Threading | Loop tunggal | Multi-thread |

## Lisensi

Internal use only.

## Kontak

Untuk pertanyaan teknis, hubungi tim pengembang AQMS.
