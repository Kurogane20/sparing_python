#!/bin/bash
# ============================================================
# AQMS Monitoring System - Raspberry Pi 4
# Script untuk menjalankan sistem monitoring
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "  AQMS Monitoring System - Raspberry Pi 4"
echo "  RS485 via GPIO (MAX485)"
echo "============================================"

# Cek apakah berjalan sebagai root (diperlukan untuk GPIO)
if [ "$EUID" -ne 0 ]; then
    echo "[WARN] Tidak berjalan sebagai root."
    echo "[WARN] GPIO mungkin memerlukan akses root."
    echo "[INFO] Jalankan dengan: sudo ./run.sh"
    echo ""
fi

# Cek UART sudah aktif
if [ -e /dev/serial0 ]; then
    echo "[OK] Serial port /dev/serial0 tersedia"
else
    echo "[ERROR] Serial port /dev/serial0 tidak ditemukan!"
    echo "[INFO] Aktifkan UART dengan:"
    echo "       sudo raspi-config -> Interface Options -> Serial Port"
    echo "       - Login shell over serial: NO"
    echo "       - Serial port hardware enabled: YES"
    echo "       Kemudian reboot."
    exit 1
fi

# Cek Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 tidak ditemukan!"
    exit 1
fi
echo "[OK] Python3: $(python3 --version)"

# Buat virtual environment jika belum ada
if [ ! -d "venv" ]; then
    echo "[INFO] Membuat virtual environment..."
    python3 -m venv venv
fi

# Aktivasi virtual environment
source venv/bin/activate

# Install dependencies
echo "[INFO] Mengecek dependencies..."
pip install -r requirements.txt --quiet 2>/dev/null

# Parse arguments
MODE="real"
EXTRA_ARGS=""

for arg in "$@"; do
    case $arg in
        --dummy|-d)
            MODE="dummy"
            EXTRA_ARGS="$EXTRA_ARGS --dummy"
            ;;
        --fullscreen|-f)
            EXTRA_ARGS="$EXTRA_ARGS --fullscreen"
            ;;
        --interval=*|-i=*)
            EXTRA_ARGS="$EXTRA_ARGS --interval ${arg#*=}"
            ;;
        *)
            EXTRA_ARGS="$EXTRA_ARGS $arg"
            ;;
    esac
done

echo "[INFO] Mode: $MODE"
echo "[INFO] Memulai AQMS..."
echo ""

# Jalankan aplikasi
python3 main.py $EXTRA_ARGS
