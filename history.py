"""
AQMS Sensor History
Penyimpanan riwayat pembacaan sensor ke SQLite lokal.

Data pengiriman ke server hilang dari tampilan setelah terkirim —
riwayat ini menyimpan semua pembacaan untuk audit / grafik / analisis
(misal deteksi drift sensor atau anomali di kemudian hari).
"""

import sqlite3
import time


class SensorHistory:
    """Simpan pembacaan sensor ke SQLite. Data lama dipangkas otomatis."""

    def __init__(self, db_file: str = "sensor_history.db", keep_days: int = 90):
        self.db_file = db_file
        self.keep_days = keep_days
        self._init_db()

    def _exec(self, sql: str, params: tuple = (), fetch: bool = False):
        """Jalankan satu statement dengan koneksi yang SELALU ditutup —
        'with sqlite3.connect()' hanya commit, tidak menutup koneksi
        (bisa mengunci file di Windows / bocor file handle)."""
        conn = sqlite3.connect(self.db_file)
        try:
            with conn:
                cur = conn.execute(sql, params)
                return cur.fetchall() if fetch else cur.rowcount
        finally:
            conn.close()

    def _init_db(self):
        try:
            self._exec("""
                CREATE TABLE IF NOT EXISTS readings (
                    ts    INTEGER PRIMARY KEY,
                    ph    REAL,
                    tss   REAL,
                    debit REAL,
                    cod   REAL,
                    nh3n  REAL
                )
            """)
            self.prune()
        except Exception as e:
            print(f"[ERROR] Init history DB gagal: {e}")

    def insert(self, d) -> None:
        """Simpan satu pembacaan (SensorData)."""
        try:
            self._exec(
                "INSERT OR REPLACE INTO readings VALUES (?,?,?,?,?,?)",
                (d.timestamp, d.ph, d.tss, d.debit, d.cod, d.nh3n)
            )
        except Exception as e:
            print(f"[ERROR] Simpan history gagal: {e}")

    def prune(self) -> None:
        """Hapus data lebih tua dari keep_days."""
        try:
            cutoff = int(time.time()) - self.keep_days * 86400
            n = self._exec("DELETE FROM readings WHERE ts < ?", (cutoff,))
            if n > 0:
                print(f"[INFO] History: {n} data lama dipangkas")
        except Exception as e:
            print(f"[ERROR] Prune history gagal: {e}")

    def recent(self, hours: int = 24) -> list:
        """Ambil pembacaan N jam terakhir — untuk grafik/analisis."""
        try:
            cutoff = int(time.time()) - hours * 3600
            return self._exec(
                "SELECT ts, ph, tss, debit, cod, nh3n FROM readings "
                "WHERE ts >= ? ORDER BY ts", (cutoff,), fetch=True
            )
        except Exception as e:
            print(f"[ERROR] Baca history gagal: {e}")
            return []
