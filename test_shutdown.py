"""systemd stop/restart mengirim SIGTERM. Tanpa handler, proses langsung mati:
blok finally di main() (worker.stop → penanda shutdown bersih, config.save)
tidak jalan, sehingga setiap restart terencana dilaporkan sebagai crash
(previous_shutdown_clean=False) dan memicu alarm "listrik tidak stabil" palsu.
"""
import os
import signal
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import main as main_mod


class TestTermHandler(unittest.TestCase):
    def tearDown(self):
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def test_sigterm_arriving_while_qt_waits_quits_the_loop(self):
        app = QApplication.instance() or QApplication([])
        keep = main_mod.install_term_handler(app)   # noqa: F841 — timer harus tetap hidup
        # Sinyal datang saat loop Qt (C++) sedang menunggu, bukan dari kode Python
        threading.Timer(0.3, lambda: signal.raise_signal(signal.SIGTERM)).start()
        QTimer.singleShot(5000, lambda: app.exit(99))   # jaring pengaman
        started = time.monotonic()
        rc = app.exec()
        self.assertEqual(rc, 0)
        self.assertLess(time.monotonic() - started, 3)


if __name__ == "__main__":
    unittest.main()
