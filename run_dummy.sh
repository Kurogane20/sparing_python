#!/bin/bash
# ============================================================
# AQMS Monitoring System - Raspberry Pi 4 (DUMMY MODE)
# Menjalankan dengan sensor simulasi untuk testing
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[INFO] Menjalankan AQMS dalam mode DUMMY (simulasi)..."

# Buat venv jika belum ada
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt --quiet 2>/dev/null

python3 main.py --dummy --interval 5 "$@"
