"""Handler event Qt yang dipasang di widget harus mengembalikan None.

mousePressEvent adalah virtual void. PyQt >= 6.11 menghentikan seluruh aplikasi
(TypeError: invalid argument to sipBadCatcherResult → SIGABRT) bila
reimplementasinya mengembalikan nilai — dan QDialog.exec() mengembalikan int.
Gejala di Pi Ajamu (PyQt 6.12.1): aplikasi tertutup setiap kali Pengaturan
atau Riwayat dibuka. Pi lama selamat hanya karena PyQt-nya lebih tua.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

import gui


class FakeDialog:
    opened = []

    def __init__(self, parent):
        FakeDialog.opened.append(type(self).__name__)

    def exec(self):
        return 1   # QDialog.exec() mengembalikan kode hasil (int)


class FakeHistory(FakeDialog):
    pass


class FakeSettings(FakeDialog):
    pass


class TestInstalledHandlers(unittest.TestCase):
    def test_every_installed_mouse_handler_returns_none(self):
        app = QApplication.instance() or QApplication([])  # noqa: F841
        FakeDialog.opened = []
        with mock.patch.object(gui, "HistoryDialog", FakeHistory), \
                mock.patch.object(gui, "SettingsDialog", FakeSettings):
            window = gui.MainWindow()
            patched = [w for w in window.findChildren(QWidget) if "mousePressEvent" in vars(w)]
            self.assertGreaterEqual(len(patched), 3)       # JENDELA, RIWAYAT, PENGATURAN
            for w in patched:
                self.assertIsNone(w.mousePressEvent(None), f"handler of {w.text() if hasattr(w, 'text') else w}")
        self.assertIn("FakeHistory", FakeDialog.opened)
        self.assertIn("FakeSettings", FakeDialog.opened)


if __name__ == "__main__":
    unittest.main()
