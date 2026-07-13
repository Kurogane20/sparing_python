"""
AQMS Anomaly Detection
Deteksi anomali pembacaan sensor secara statistik — ringan, tanpa
dependensi ML (cocok untuk Raspberry Pi).

Dua jenis deteksi:
1. Lonjakan (spike) — nilai menyimpang ekstrem dari median jendela
   terakhir, diukur dengan robust z-score berbasis MAD (Median Absolute
   Deviation). Tahan terhadap outlier di jendela itu sendiri.
2. Macet (stuck) — nilai persis sama berulang kali beruntun; indikasi
   sensor beku, register tidak ter-update, atau kabel bermasalah.

Anomali TIDAK mengubah data — data tetap dikirim apa adanya ke server.
Deteksi hanya memberi peringatan ke operator lewat log GUI.
"""

from collections import deque
from typing import List


class _Track:
    """Riwayat nilai satu sensor."""
    def __init__(self, window: int):
        self.values = deque(maxlen=window)
        self.stuck_warned = False


class AnomalyDetector:
    """Deteksi spike (MAD robust z-score) dan stuck value per sensor."""

    WINDOW = 30       # jendela riwayat per sensor (≈ 1 jam @ 2 menit)
    MIN_SAMPLES = 10  # minimal data sebelum deteksi spike aktif
    K = 5.0           # ambang robust z-score (5 = sangat konservatif)
    STUCK_N = 10      # nilai identik beruntun → macet (≈ 20 menit)

    def __init__(self):
        self._tracks = {}

    def _track(self, name: str) -> _Track:
        if name not in self._tracks:
            self._tracks[name] = _Track(self.WINDOW)
        return self._tracks[name]

    @staticmethod
    def _median(xs) -> float:
        s = sorted(xs)
        n = len(s)
        m = n // 2
        return s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0

    def check(self, name: str, value: float, ok: bool) -> List[str]:
        """
        Periksa satu nilai sensor terhadap riwayatnya.
        Returns: daftar pesan anomali (kosong = normal).
        """
        msgs = []
        t = self._track(name)

        if not ok:
            # Pembacaan gagal bukan anomali nilai — jangan cemari jendela
            return msgs

        prev = list(t.values)

        # 1. Spike — robust z-score MAD terhadap jendela sebelumnya
        if len(prev) >= self.MIN_SAMPLES:
            med = self._median(prev)
            mad = self._median([abs(x - med) for x in prev])
            if mad > 1e-9:
                rz = 0.6745 * abs(value - med) / mad
                if rz > self.K:
                    msgs.append(f"{name} lonjakan: {value:.2f} (median {med:.2f})")

        # 2. Stuck — STUCK_N nilai persis sama beruntun (peringatan sekali,
        #    tidak diulang setiap pembacaan sampai nilai berubah lagi)
        recent = prev[-(self.STUCK_N - 1):] + [value]
        if len(recent) >= self.STUCK_N and all(v == recent[0] for v in recent):
            if not t.stuck_warned:
                msgs.append(f"{name} macet di {value:.2f} ({self.STUCK_N}x sama)")
                t.stuck_warned = True
        else:
            t.stuck_warned = False

        t.values.append(value)
        return msgs

    def check_all(self, d) -> List[str]:
        """Periksa semua sensor dari satu SensorData."""
        msgs = []
        msgs += self.check("pH",    d.ph,    bool(d.ph_ok))
        msgs += self.check("TSS",   d.tss,   bool(d.tss_ok))
        msgs += self.check("Debit", d.debit, bool(d.debit_ok))
        msgs += self.check("COD",   d.cod,   bool(d.cod_ok))
        if d.nh3n_ok is not None:  # None = sensor tidak terpasang
            msgs += self.check("NH3-N", d.nh3n, bool(d.nh3n_ok))
        return msgs
