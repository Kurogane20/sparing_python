"""
SPARING Display GUI - v5
Target: 7" 1024x600 display, Raspberry Pi OS
"""

import sys
import platform
import random
import subprocess
from datetime import datetime
from typing import List, Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QProgressBar, QCheckBox,
    QScrollArea,
    QDialog, QLineEdit, QDoubleSpinBox, QPushButton, QMessageBox,
    QTabWidget, QFormLayout, QGroupBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QPainterPath, QLinearGradient, QBrush

from config import config
from models import SensorData, OperationalStatus, OperationalState
from history import SensorHistory

# ── RPi helpers ───────────────────────────────────────────────

def _rfile(path):
    try:
        with open(path) as f: return f.read().strip()
    except Exception: return None

def get_cpu_temp() -> str:
    v = _rfile("/sys/class/thermal/thermal_zone0/temp")
    return f"{int(v)/1000:.1f}C" if v else "N/A"

def get_mem() -> str:
    try:
        with open("/proc/meminfo") as f: lines = f.readlines()
        total = int(lines[0].split()[1])
        avail = int(lines[2].split()[1])
        return f"{(total-avail)/total*100:.0f}%"
    except Exception: return "N/A"

def get_rpi_voltage() -> str:
    try:
        r = subprocess.run(
            ["vcgencmd", "measure_volts", "core"],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0:
            return r.stdout.strip().replace("volt=", "")
    except Exception:
        pass
    return "N/A"

def get_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception: return "N/A"

# ── Theme ─────────────────────────────────────────────────────

class T:
    BG      = "#111318"
    CARD    = "#191d28"
    PANEL   = "#1d2133"
    BORDER  = "#2d3252"
    BORDER2 = "#232738"

    FG1 = "#c8d4f0"   # primary text — slightly blue-tinted
    FG2 = "#7888b0"   # secondary — 4.6:1 contrast on PANEL ✓ WCAG AA
    FG3 = "#6a7a90"   # tertiary — 3.75:1 contrast on PANEL (fix dari v4)

    AMBER  = "#e0a030"   # primary accent — industrial amber
    GREEN  = "#28c76a"
    BLUE   = "#4a90d9"
    RED    = "#e84040"
    CYAN   = "#22bfd4"

    OK   = "#28c76a"
    WARN = "#e0a030"
    ERR  = "#e84040"
    OFF  = "#4d5878"

    FONT = "DejaVu Sans" if platform.system() == "Linux" else "Segoe UI"
    MONO = "DejaVu Sans Mono" if platform.system() == "Linux" else "Consolas"

    SENSORS = {
        "pH":    (CYAN,  "",      6.0, 9.0),
        "TSS":   (AMBER, "mg/L", 0,   100),
        "DEBIT": (BLUE,  "m3/m", 0,   100),
        "COD":   (RED,   "mg/L", 0,   300),
        "NH3-N": (GREEN, "mg/L", 0,   10),
    }

    @staticmethod
    def rgba(hex_color: str, alpha: int) -> str:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return f"rgba({r},{g},{b},{alpha/255:.2f})"

# ── Signals ───────────────────────────────────────────────────

class SignalBridge(QObject):
    sensor_update     = pyqtSignal(object)
    connection_update = pyqtSignal(bool)
    notification      = pyqtSignal(str, int)
    data_count_update = pyqtSignal(int, int)
    daily_data_update = pyqtSignal(int)
    status_update     = pyqtSignal(int)
    log_entry         = pyqtSignal(str)
    secret_key_update = pyqtSignal(str, str)   # (preview_key1, preview_key2)
    modbus_log        = pyqtSignal(str)
    server_status_update = pyqtSignal(bool, bool)  # (sukses_server1, sukses_server2)
    backup_count_update  = pyqtSignal(int)         # jumlah data backup pending

# ── Sparkline ─────────────────────────────────────────────────

class Spark(QWidget):
    def __init__(self, color: str, h=34, parent=None):
        super().__init__(parent)
        self.clr = QColor(color)
        self.pts: List[float] = [0.0] * 30
        self.setFixedHeight(h)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def push(self, v: float):
        self.pts.pop(0); self.pts.append(v); self.update()

    def paintEvent(self, _):
        try:
            w, h = self.width(), self.height()
            if w <= 0 or h <= 0:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            mn, mx = min(self.pts), max(self.pts)
            rng = mx - mn if mx != mn else 1.0
            step = w / (len(self.pts) - 1)
            pts = [QPoint(int(i * step), int(h - (v - mn) / rng * (h - 4) - 2))
                   for i, v in enumerate(self.pts)]
            path = QPainterPath()
            path.moveTo(float(pts[0].x()), float(pts[0].y()))
            for i in range(len(pts) - 1):
                cx = float((pts[i].x() + pts[i+1].x()) / 2)
                path.cubicTo(cx, float(pts[i].y()), cx, float(pts[i+1].y()),
                             float(pts[i+1].x()), float(pts[i+1].y()))
            fill = QPainterPath(path)
            fill.lineTo(float(w), float(h))
            fill.lineTo(0.0, float(h))
            fill.closeSubpath()
            g = QLinearGradient(0, 0, 0, h)
            c = self.clr
            g.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 38))
            g.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
            p.fillPath(fill, QBrush(g))
            p.setPen(QPen(self.clr, 1.2))
            p.drawPath(path)
            p.end()
        except Exception as e:
            print(f"[WARN] Spark.paintEvent error: {e}")

# ── Status Tag ────────────────────────────────────────────────

class StatusTag(QLabel):
    """Rectangular tag — sharp corners (border-radius:2px), no pill."""
    _COLORS = {"NORMAL": T.GREEN, "WARNING": T.WARN, "ALARM": T.ERR}

    def __init__(self, parent=None):
        super().__init__("NORMAL", parent)
        self.setFixedSize(70, 17)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set("NORMAL")

    def set(self, s: str):
        s = s.upper()
        c = self._COLORS.get(s, T.OFF)
        self.setText(s)
        self.setStyleSheet(
            f"color:{c};font-weight:bold;font-size:8px;"
            f"font-family:'{T.MONO}';letter-spacing:0.5px;"
            f"border:1px solid {c};border-radius:2px;"
            f"background:{T.rgba(c, 22)};"
        )

# ── Sensor Card ───────────────────────────────────────────────

class SensorCard(QFrame):
    """
    Fix dari v4:
    - border-left CSS trick (tidak ada konflik corner dengan QFrame terpisah)
    - border-radius:0 (industrial — sharp corners)
    - value font dikecilkan ke 36px agar angka 6-digit muat di ~130px lebar
    """
    def __init__(self, name, color, unit, lo, hi, parent=None):
        super().__init__(parent)
        self.lo, self.hi = lo, hi

        # border-left override setelah border shorthand — bekerja di Qt
        self.setStyleSheet(f"""
            SensorCard {{
                background: {T.CARD};
                border: 1px solid {T.BORDER};
                border-left: 3px solid {color};
                border-radius: 0px;
            }}
        """)

        vb = QVBoxLayout(self)
        vb.setContentsMargins(10, 8, 8, 6)
        vb.setSpacing(0)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        tl = QLabel(name)
        tl.setStyleSheet(
            f"color:{T.FG2};font-size:10px;font-weight:bold;"
            f"font-family:'{T.FONT}';letter-spacing:1px;"
        )
        self.tag = StatusTag()
        hdr.addWidget(tl); hdr.addStretch(); hdr.addWidget(self.tag)
        vb.addLayout(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{T.BORDER2};border:none;margin-top:5px;margin-bottom:4px;")
        vb.addWidget(sep)

        vb.addStretch(2)

        # Nilai utama — tanpa QGraphicsDropShadowEffect
        self.val = QLabel("---")
        self.val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.val.setStyleSheet(
            f"color:{color};font-size:36px;font-weight:bold;"
            f"font-family:'{T.MONO}';"
        )
        vb.addWidget(self.val)

        self.unit_lbl = QLabel(unit)
        self.unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unit_lbl.setStyleSheet(f"color:{T.FG3};font-size:10px;")
        vb.addWidget(self.unit_lbl)

        # "NO DATA" → diganti timestamp saat data masuk — lebih komunikatif
        self._ts_lbl = QLabel("NO DATA")
        self._ts_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ts_lbl.setStyleSheet(
            f"color:{T.FG3};font-size:9px;font-family:'{T.MONO}';"
        )
        vb.addWidget(self._ts_lbl)

        vb.addStretch(2)

        lim = QLabel(f"Baku mutu  {lo} – {hi} {unit}")
        lim.setStyleSheet(f"color:{T.FG3};font-size:9px;")
        vb.addWidget(lim)
        vb.addSpacing(3)

        self.spark = Spark(color)
        vb.addWidget(self.spark)

    def update_value(self, v: float, ts: Optional[int] = None):
        # Adaptif: 1 desimal untuk angka besar agar tidak overflow di 130px
        self.val.setText(f"{v:.1f}" if abs(v) >= 100 else f"{v:.2f}")
        self.spark.push(v)
        if ts:
            self._ts_lbl.setText(datetime.fromtimestamp(ts).strftime("%H:%M:%S"))
        if self.lo <= v <= self.hi:
            self.tag.set("NORMAL")
        elif v > self.hi * 1.2:
            self.tag.set("ALARM")
        else:
            self.tag.set("WARNING")

# ── Sidebar Section ───────────────────────────────────────────

class Section(QFrame):
    def __init__(self, title, color=None, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background:{T.PANEL};
                border:1px solid {T.BORDER};
                border-radius:2px;
            }}
        """)
        self._vb = QVBoxLayout(self)
        self._vb.setContentsMargins(8, 6, 8, 6)
        self._vb.setSpacing(3)

        c = color or T.FG3
        hdr_row = QHBoxLayout(); hdr_row.setSpacing(5)
        sq = QFrame()
        sq.setFixedSize(3, 11)
        sq.setStyleSheet(f"background:{c};border:none;")
        th = QLabel(title.upper())
        th.setStyleSheet(
            f"color:{c};font-weight:bold;font-size:10px;"
            f"letter-spacing:1px;font-family:'{T.FONT}';"
        )
        hdr_row.addWidget(sq); hdr_row.addWidget(th); hdr_row.addStretch()
        self._vb.addLayout(hdr_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{T.BORDER};border:none;margin-bottom:1px;")
        self._vb.addWidget(sep)

    def row(self, label, val="–", vc=None) -> QLabel:
        vc = vc or T.FG1
        hl = QHBoxLayout(); hl.setSpacing(4)
        lb = QLabel(label)
        # FG2 (bukan FG3) — memastikan label terbaca — fix dari v4
        lb.setStyleSheet(f"color:{T.FG2};font-size:10px;")
        vl = QLabel(val)
        vl.setStyleSheet(
            f"color:{vc};font-size:11px;font-weight:bold;"
            f"font-family:'{T.MONO}';"
        )
        vl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(lb); hl.addStretch(); hl.addWidget(vl)
        self._vb.addLayout(hl)
        return vl

    def add(self, w): self._vb.addWidget(w)

# ── Settings Dialog ───────────────────────────────────────────

_DS = f"""
QDialog{{background:{T.BG};}}
QTabWidget::pane{{border:1px solid {T.BORDER};background:{T.PANEL};border-radius:2px;}}
QTabBar::tab{{background:{T.CARD};color:{T.FG2};border:1px solid {T.BORDER};padding:5px 14px;margin-right:2px;}}
QTabBar::tab:selected{{background:{T.PANEL};color:{T.AMBER};border-bottom:2px solid {T.AMBER};}}
QGroupBox{{color:{T.FG1};border:1px solid {T.BORDER};border-radius:2px;margin-top:8px;padding-top:12px;font-weight:bold;font-size:11px;}}
QGroupBox::title{{subcontrol-origin:margin;left:8px;padding:0 4px;}}
QLabel{{color:{T.FG2};font-size:11px;}}
QLineEdit{{background:{T.CARD};color:{T.FG1};border:1px solid {T.BORDER};border-radius:2px;padding:9px 10px;font-size:11px;}}
QLineEdit:focus{{border:1px solid {T.AMBER};}}
QDoubleSpinBox{{background:{T.CARD};color:{T.FG1};border:1px solid {T.BORDER};border-radius:2px;padding:9px 10px;font-size:12px;font-weight:bold;}}
QDoubleSpinBox:focus{{border:1px solid {T.AMBER};}}
QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:30px;border:none;background:{T.BORDER};}}
QDoubleSpinBox::up-button:hover,QDoubleSpinBox::down-button:hover{{background:{T.AMBER};}}
QPushButton{{background:{T.CARD};color:{T.FG1};border:1px solid {T.BORDER};border-radius:2px;padding:10px 16px;font-size:11px;font-weight:bold;}}
QPushButton:hover{{border:1px solid {T.AMBER};color:{T.AMBER};}}
QPushButton#S{{background:{T.OK};color:#000;border:none;}}
QPushButton#S:hover{{background:#34d87a;}}
QPushButton#W{{background:{T.BLUE};color:white;border:none;}}
QPushButton#W:hover{{background:#5aa0e8;}}
"""

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pengaturan Sistem")
        self.setFixedSize(520, 470)
        self.setStyleSheet(_DS)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10); root.setSpacing(8)

        hd = QLabel("PENGATURAN SISTEM")
        hd.setStyleSheet(
            f"color:{T.FG1};font-size:13px;font-weight:bold;letter-spacing:2px;"
        )
        hd.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hd)

        tabs = QTabWidget()
        tabs.addTab(self._t_server(), "Server KLHK")
        tabs.addTab(self._t_wifi(),   "WiFi")
        tabs.addTab(self._t_offset(), "Offset Sensor")
        tabs.addTab(self._t_params(), "Parameter")
        tabs.addTab(self._t_system(), "Sistem")
        root.addWidget(tabs)

        bl = QHBoxLayout(); bl.addStretch()
        bc = QPushButton("Batal"); bc.clicked.connect(self.reject)
        bs = QPushButton("Simpan"); bs.setObjectName("S"); bs.clicked.connect(self._save)
        bl.addWidget(bc); bl.addWidget(bs)
        root.addLayout(bl)

    def _t_server(self):
        w = QWidget(); vb = QVBoxLayout(w); vb.setSpacing(8)
        g = QGroupBox("Server 2 – KLHK")
        fm = QFormLayout(g); fm.setSpacing(8); fm.setContentsMargins(10,16,10,10)
        self.e_uid = QLineEdit(config.server.uid_2)
        self.e_url = QLineEdit(config.server.server_url_2)
        self.e_sk  = QLineEdit(config.server.secret_key_url_2)
        fm.addRow("UID KLHK:", self.e_uid)
        fm.addRow("Server URL:", self.e_url)
        fm.addRow("Secret Key URL:", self.e_sk)
        vb.addWidget(g)
        note = QLabel("Perubahan aktif setelah restart aplikasi.")
        note.setStyleSheet(f"color:{T.WARN};font-size:10px;")
        vb.addWidget(note); vb.addStretch(); return w

    def _t_wifi(self):
        w = QWidget(); vb = QVBoxLayout(w); vb.setSpacing(8)
        g = QGroupBox("Koneksi WiFi")
        fm = QFormLayout(g); fm.setSpacing(8); fm.setContentsMargins(10,16,10,10)
        self.e_ssid = QLineEdit(config.network.wifi_ssid)
        self.e_pass = QLineEdit(config.network.wifi_password)
        self.e_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._btn_show = QPushButton("Tampilkan")
        self._btn_show.setFixedWidth(86)
        self._btn_show.clicked.connect(self._toggle_pw)
        fm.addRow("SSID:", self.e_ssid)
        fm.addRow("Sandi:", self.e_pass)
        fm.addRow("", self._btn_show)
        vb.addWidget(g)
        bc = QPushButton("Hubungkan WiFi Sekarang")
        bc.setObjectName("W"); bc.clicked.connect(self._wifi)
        vb.addWidget(bc)
        self._ws = QLabel("Status: –")
        self._ws.setStyleSheet(f"color:{T.FG2};font-size:10px;")
        vb.addWidget(self._ws)
        note = QLabel("Menggunakan nmcli (NetworkManager).")
        note.setStyleSheet(f"color:{T.FG3};font-size:9px;")
        vb.addWidget(note); vb.addStretch(); return w

    def _t_offset(self):
        w = QWidget(); vb = QVBoxLayout(w); vb.setSpacing(8)
        g = QGroupBox("Faktor Kalibrasi Sensor")
        fm = QFormLayout(g); fm.setSpacing(8); fm.setContentsMargins(10,16,10,10)

        def spin(lo, hi, step, val, suffix):
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setDecimals(3)
            s.setSingleStep(step); s.setValue(val); s.setSuffix(suffix)
            return s

        self.s_ph  = spin(0.001, 10.0, 0.001, config.offsets.ph_factor,    "  ×")
        self.s_tss = spin(0.001, 99.9, 0.001, config.offsets.tss_factor,   "  ×")
        self.s_dbt = spin(0.001, 99.9, 0.001, config.offsets.debit_factor,  "  ×")
        fm.addRow("Faktor pH:", self.s_ph)
        fm.addRow("Faktor TSS:", self.s_tss)
        fm.addRow("Faktor Debit:", self.s_dbt)
        vb.addWidget(g)
        br = QPushButton("Reset Semua ke 1.0")
        br.clicked.connect(lambda: [s.setValue(1.0) for s in [self.s_ph, self.s_tss, self.s_dbt]])
        vb.addWidget(br)
        note = QLabel("nilai × faktor  |  pH hasil di-clamp ke 0–14  |  Default: 1.0 (tanpa koreksi)")
        note.setStyleSheet(f"color:{T.FG3};font-size:9px;"); note.setWordWrap(True)
        vb.addWidget(note)
        vb.addStretch(); return w

    def _t_params(self):
        w = QWidget(); vb = QVBoxLayout(w); vb.setSpacing(8)
        g = QGroupBox("Parameter yang Ditampilkan")
        gvb = QVBoxLayout(g); gvb.setSpacing(8); gvb.setContentsMargins(10,16,10,10)
        INFO = {
            "pH":    "Derajat keasaman air",
            "TSS":   "Total Suspended Solid (mg/L)",
            "DEBIT": "Laju aliran air (m3/jam)",
            "COD":   "Chemical Oxygen Demand (mg/L)",
            "NH3-N": "Amonia nitrogen (mg/L)",
        }
        self._param_checks = {}
        for name in config.ALL_SENSORS:
            row = QHBoxLayout(); row.setSpacing(8)
            cb = QCheckBox(name)
            cb.setChecked(name in config.display_sensors)
            cb.setStyleSheet(f"color:{T.FG1};font-size:11px;font-weight:bold;")
            desc = QLabel(INFO.get(name, ""))
            desc.setStyleSheet(f"color:{T.FG3};font-size:9px;")
            row.addWidget(cb); row.addWidget(desc); row.addStretch()
            gvb.addLayout(row)
            self._param_checks[name] = cb
        vb.addWidget(g)

        g2 = QGroupBox("Konfigurasi Sensor Debit")
        g2vb = QVBoxLayout(g2); g2vb.setSpacing(6); g2vb.setContentsMargins(10,16,10,10)
        self._cb_closed = QCheckBox("Saluran Tertutup (Closed Channel)")
        self._cb_closed.setChecked(config.modbus.debit_closed_channel)
        self._cb_closed.setStyleSheet(f"color:{T.FG1};font-size:11px;font-weight:bold;")
        desc_closed = QLabel("Centang: Float 32-bit reg[1]<<16|reg[0]  |  Kosong: Double 64-bit reg 15-18")
        desc_closed.setStyleSheet(f"color:{T.FG3};font-size:9px;"); desc_closed.setWordWrap(True)
        g2vb.addWidget(self._cb_closed); g2vb.addWidget(desc_closed)
        vb.addWidget(g2)

        g3 = QGroupBox("Konfigurasi Sensor COD")
        g3vb = QVBoxLayout(g3); g3vb.setSpacing(6); g3vb.setContentsMargins(10,16,10,10)
        self._cb_cod_int = QCheckBox("Integer/10 (Arduino ModbusMaster)")
        self._cb_cod_int.setChecked(config.modbus.cod_integer_mode)
        self._cb_cod_int.setStyleSheet(f"color:{T.FG1};font-size:11px;font-weight:bold;")
        desc_cod = QLabel("Centang: reg[0]/10.0  |  Kosong: Float CDAB reg[1]<<16|reg[0]")
        desc_cod.setStyleSheet(f"color:{T.FG3};font-size:9px;"); desc_cod.setWordWrap(True)
        g3vb.addWidget(self._cb_cod_int); g3vb.addWidget(desc_cod)
        vb.addWidget(g3)

        g4 = QGroupBox("Konfigurasi Sensor NH3-N")
        g4vb = QVBoxLayout(g4); g4vb.setSpacing(6); g4vb.setContentsMargins(10,16,10,10)
        self._cb_nh3n = QCheckBox(f"Sensor NH3-N terpasang (Slave {config.modbus.nh3n_slave_id})")
        self._cb_nh3n.setChecked(config.modbus.nh3n_enabled)
        self._cb_nh3n.setStyleSheet(f"color:{T.FG1};font-size:11px;font-weight:bold;")
        desc_nh3n = QLabel("Kosong: NH3-N dikirim 0.00 dan LED abu-abu  |  Format: Float CDAB reg 0-1")
        desc_nh3n.setStyleSheet(f"color:{T.FG3};font-size:9px;"); desc_nh3n.setWordWrap(True)
        g4vb.addWidget(self._cb_nh3n); g4vb.addWidget(desc_nh3n)
        vb.addWidget(g4)

        g5 = QGroupBox("Deteksi Anomali")
        g5vb = QVBoxLayout(g5); g5vb.setSpacing(6); g5vb.setContentsMargins(10,16,10,10)
        self._cb_anomaly = QCheckBox("Deteksi lonjakan / sensor macet")
        self._cb_anomaly.setChecked(config.modbus.anomaly_enabled)
        self._cb_anomaly.setStyleSheet(f"color:{T.FG1};font-size:11px;font-weight:bold;")
        desc_an = QLabel("Peringatan di log — data tetap dikirim apa adanya")
        desc_an.setStyleSheet(f"color:{T.FG3};font-size:9px;"); desc_an.setWordWrap(True)
        g5vb.addWidget(self._cb_anomaly); g5vb.addWidget(desc_an)
        vb.addWidget(g5)

        note = QLabel("Min. 1 parameter harus aktif.")
        note.setStyleSheet(f"color:{T.FG3};font-size:9px;"); note.setWordWrap(True)
        vb.addWidget(note); vb.addStretch()

        # Konten tab sudah lebih tinggi dari dialog — bungkus dengan scroll
        sa = QScrollArea()
        sa.setWidget(w); sa.setWidgetResizable(True)
        sa.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        return sa

    def _t_system(self):
        w = QWidget(); vb = QVBoxLayout(w); vb.setSpacing(8)
        g = QGroupBox("Daya Perangkat")
        gvb = QVBoxLayout(g); gvb.setSpacing(8); gvb.setContentsMargins(10, 16, 10, 10)

        note = QLabel(
            "Matikan perangkat lewat tombol ini sebelum mencabut listrik — "
            "mencegah korupsi SD card."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{T.FG3};font-size:10px;")
        gvb.addWidget(note)

        b_rb = QPushButton("RESTART PERANGKAT")
        b_rb.setFixedHeight(48)
        b_rb.setStyleSheet(
            f"QPushButton{{background:{T.rgba(T.WARN,28)};color:{T.WARN};"
            f"border:1px solid {T.rgba(T.WARN,100)};border-radius:2px;"
            f"font-size:11px;font-weight:bold;font-family:'{T.MONO}';}}"
            f"QPushButton:hover{{background:{T.rgba(T.WARN,60)};}}"
        )
        b_rb.clicked.connect(self._reboot)
        gvb.addWidget(b_rb)

        b_off = QPushButton("MATIKAN PERANGKAT")
        b_off.setFixedHeight(48)
        b_off.setStyleSheet(
            f"QPushButton{{background:{T.rgba(T.ERR,28)};color:{T.ERR};"
            f"border:1px solid {T.rgba(T.ERR,100)};border-radius:2px;"
            f"font-size:11px;font-weight:bold;font-family:'{T.MONO}';}}"
            f"QPushButton:hover{{background:{T.rgba(T.ERR,60)};}}"
        )
        b_off.clicked.connect(self._shutdown)
        gvb.addWidget(b_off)

        vb.addWidget(g); vb.addStretch(); return w

    def _power_cmd(self, label: str, cmd: list):
        reply = QMessageBox.question(
            self, "Konfirmasi",
            f"{label}?\n\nAplikasi akan berhenti dan data buffer tersimpan aman di disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if platform.system() != "Linux":
            QMessageBox.information(self, "Info", "Hanya tersedia di Linux/Raspberry Pi.")
            return
        try:
            subprocess.run(cmd, timeout=10)
        except Exception as e:
            QMessageBox.critical(self, "Gagal", str(e))

    def _reboot(self):
        self._power_cmd("Restart perangkat", ["sudo", "reboot"])

    def _shutdown(self):
        self._power_cmd("Matikan perangkat", ["sudo", "shutdown", "-h", "now"])

    def _toggle_pw(self):
        if self.e_pass.echoMode() == QLineEdit.EchoMode.Password:
            self.e_pass.setEchoMode(QLineEdit.EchoMode.Normal)
            self._btn_show.setText("Sembunyikan")
        else:
            self.e_pass.setEchoMode(QLineEdit.EchoMode.Password)
            self._btn_show.setText("Tampilkan")

    def _wifi(self):
        ssid = self.e_ssid.text().strip()
        pw   = self.e_pass.text().strip()
        if not ssid:
            self._setwstatus("SSID kosong!", T.ERR); return
        self._setwstatus("Menghubungkan...", T.WARN)
        try:
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
            if pw: cmd += ["password", pw]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                self._setwstatus(f"Terhubung ke {ssid}", T.OK)
            else:
                self._setwstatus((r.stderr or r.stdout).strip()[:80], T.ERR)
        except FileNotFoundError:
            self._setwstatus("nmcli tidak ditemukan", T.ERR)
        except subprocess.TimeoutExpired:
            self._setwstatus("Waktu habis", T.ERR)
        except Exception as e:
            self._setwstatus(str(e)[:70], T.ERR)

    def _setwstatus(self, msg, color):
        self._ws.setText(f"Status: {msg}")
        self._ws.setStyleSheet(f"color:{color};font-size:10px;")

    def _save(self):
        try:
            config.server.uid_2            = self.e_uid.text().strip()
            config.server.server_url_2     = self.e_url.text().strip()
            config.server.secret_key_url_2 = self.e_sk.text().strip()
            config.network.wifi_ssid       = self.e_ssid.text().strip()
            config.network.wifi_password   = self.e_pass.text().strip()
            config.offsets.ph_factor       = self.s_ph.value()
            config.offsets.tss_factor      = self.s_tss.value()
            config.offsets.debit_factor    = self.s_dbt.value()
            selected = [n for n, cb in self._param_checks.items() if cb.isChecked()]
            if selected:
                config.display_sensors = selected
            config.modbus.debit_closed_channel = self._cb_closed.isChecked()
            config.modbus.cod_integer_mode     = self._cb_cod_int.isChecked()
            config.modbus.nh3n_enabled         = self._cb_nh3n.isChecked()
            config.modbus.anomaly_enabled      = self._cb_anomaly.isChecked()
            config.save()
        except Exception as e:
            QMessageBox.critical(self, "Gagal Menyimpan", str(e))
            return

        mw = self.parent()
        if hasattr(mw, 'refresh_sidebar'):
            mw.refresh_sidebar()

        # Tutup dialog DULU sebelum notifikasi apapun.
        # QMessageBox dari dalam exec() dialog membuat 3 nested event loop
        # → menyebabkan aplikasi keluar di PyQt6/Windows.
        self.accept()

        # Notifikasi lewat notification bar MainWindow (bukan QMessageBox).
        if mw and hasattr(mw, 'signal_bridge'):
            mw.signal_bridge.notification.emit("Pengaturan berhasil disimpan", 3000)

# ── History Chart ─────────────────────────────────────────────

class HistoryChart(QWidget):
    """Grafik garis riwayat sensor — QPainter, satu seri per tampilan.

    Mengikuti kaidah dataviz: garis 2px, grid samar, teks netral (bukan
    warna seri), garis ambang baku mutu putus-putus, hover crosshair +
    tooltip, dan empty state.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list = []        # [(ts, value)] data mentah
        self.clr = QColor(T.BLUE)
        self.unit = ""
        self.limit_hi = None        # ambang baku mutu atas
        self._pts: list = []        # [(x, y, ts, v)] hasil layout
        self._hover = None          # index titik terdekat kursor
        self.setMinimumHeight(280)
        self.setMouseTracking(True)
        self.setStyleSheet(f"background:{T.CARD};")

    def set_data(self, rows, color: str, unit: str, limit_hi):
        self.rows = [(ts, v) for ts, v in rows if v is not None]
        self.clr = QColor(color)
        self.unit = unit
        self.limit_hi = limit_hi
        self._hover = None
        self.update()

    # ── layout ──
    _L, _R, _T, _B = 56, 14, 14, 28

    def _layout(self, w, h):
        pw = w - self._L - self._R
        ph = h - self._T - self._B
        vals = [v for _, v in self.rows]
        mn, mx = min(vals), max(vals)
        if self.limit_hi is not None:
            mx = max(mx, self.limit_hi)
        pad = (mx - mn) * 0.08 or 1.0
        mn, mx = mn - pad, mx + pad

        # Downsample: maks 1 titik per piksel (rata-rata per bucket)
        rows = self.rows
        if len(rows) > pw > 0:
            step = len(rows) / pw
            ds, i = [], 0.0
            while int(i) < len(rows):
                chunk = rows[int(i):max(int(i + step), int(i) + 1)]
                ts_mid = chunk[len(chunk) // 2][0]
                avg = sum(v for _, v in chunk) / len(chunk)
                ds.append((ts_mid, avg))
                i += step
            rows = ds

        n = max(len(rows) - 1, 1)
        self._pts = [
            (self._L + pw * i / n,
             self._T + ph * (1 - (v - mn) / (mx - mn)),
             ts, v)
            for i, (ts, v) in enumerate(rows)
        ]
        return mn, mx, pw, ph

    def paintEvent(self, _):
        try:
            w, h = self.width(), self.height()
            if w <= 0 or h <= 0:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(0, 0, w, h, QColor(T.CARD))

            if len(self.rows) < 2:
                p.setPen(QPen(QColor(T.FG3)))
                p.setFont(QFont(T.FONT, 10))
                p.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter,
                           "Belum ada data riwayat untuk rentang ini")
                p.end()
                return

            mn, mx, pw, ph = self._layout(w, h)

            # Grid horizontal samar + label nilai (teks netral)
            p.setFont(QFont(T.MONO, 8))
            for i in range(5):
                y = self._T + ph * i / 4
                p.setPen(QPen(QColor(T.BORDER2), 1))
                p.drawLine(self._L, int(y), w - self._R, int(y))
                val = mx - (mx - mn) * i / 4
                fmt = f"{val:.1f}" if abs(val) >= 100 else f"{val:.2f}"
                p.setPen(QPen(QColor(T.FG3)))
                p.drawText(0, int(y) - 7, self._L - 6, 14,
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt)

            # Label waktu: awal / tengah / akhir
            span = self.rows[-1][0] - self.rows[0][0]
            tfmt = "%d/%m %H:%M" if span > 86400 else "%H:%M"
            for frac, align in ((0, Qt.AlignmentFlag.AlignLeft),
                                (0.5, Qt.AlignmentFlag.AlignHCenter),
                                (1, Qt.AlignmentFlag.AlignRight)):
                idx = int(frac * (len(self._pts) - 1))
                ts = self._pts[idx][2]
                p.setPen(QPen(QColor(T.FG3)))
                p.drawText(self._L, h - self._B + 6, pw, 16,
                           align, datetime.fromtimestamp(ts).strftime(tfmt))

            # Garis ambang baku mutu (putus-putus)
            if self.limit_hi is not None and mn < self.limit_hi < mx:
                y = self._T + ph * (1 - (self.limit_hi - mn) / (mx - mn))
                pen = QPen(QColor(T.ERR), 1, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(self._L, int(y), w - self._R, int(y))
                p.setPen(QPen(QColor(T.FG2)))
                p.drawText(self._L + 4, int(y) - 4, f"BM {self.limit_hi:g}")

            # Garis data 2px
            p.setPen(QPen(self.clr, 2))
            path = QPainterPath()
            path.moveTo(self._pts[0][0], self._pts[0][1])
            for x, y, _, _v in self._pts[1:]:
                path.lineTo(x, y)
            p.drawPath(path)

            # Hover: crosshair + marker + tooltip
            if self._hover is not None and 0 <= self._hover < len(self._pts):
                x, y, ts, v = self._pts[self._hover]
                p.setPen(QPen(QColor(T.FG3), 1, Qt.PenStyle.DotLine))
                p.drawLine(int(x), self._T, int(x), h - self._B)
                # marker dengan ring warna permukaan
                p.setPen(QPen(QColor(T.CARD), 2))
                p.setBrush(self.clr)
                p.drawEllipse(QPoint(int(x), int(y)), 5, 5)
                # tooltip
                txt = (f"{datetime.fromtimestamp(ts).strftime('%d/%m %H:%M')}   "
                       f"{v:.2f} {self.unit}".rstrip())
                p.setFont(QFont(T.MONO, 9))
                tw = p.fontMetrics().horizontalAdvance(txt) + 16
                tx = min(max(int(x) - tw // 2, self._L), w - self._R - tw)
                p.setPen(QPen(QColor(T.BORDER), 1))
                p.setBrush(QColor(T.PANEL))
                p.drawRect(tx, 4, tw, 20)
                p.setPen(QPen(QColor(T.FG1)))
                p.drawText(tx, 4, tw, 20, Qt.AlignmentFlag.AlignCenter, txt)

            p.end()
        except Exception as e:
            print(f"[WARN] HistoryChart.paintEvent error: {e}")

    def mouseMoveEvent(self, ev):
        if not self._pts:
            return
        mx = ev.position().x()
        nearest = min(range(len(self._pts)), key=lambda i: abs(self._pts[i][0] - mx))
        if nearest != self._hover:
            self._hover = nearest
            self.update()

    def leaveEvent(self, _):
        self._hover = None
        self.update()


class HistoryDialog(QDialog):
    """Riwayat pembacaan sensor dari SQLite — satu parameter per tampilan."""

    _COL = {"pH": 1, "TSS": 2, "DEBIT": 3, "COD": 4, "NH3-N": 5}
    _RANGES = [("6 JAM", 6), ("24 JAM", 24), ("7 HARI", 168)]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Riwayat Sensor")
        self.setFixedSize(960, 540)
        self.setStyleSheet(_DS + f"""
            QPushButton[selector="true"]{{padding:6px 12px;font-family:'{T.MONO}';}}
            QPushButton[selector="true"]:checked{{
                border:1px solid {T.AMBER};color:{T.AMBER};
                background:{T.rgba(T.AMBER, 25)};}}
        """)
        self._hist = SensorHistory()
        self._param = "pH"
        self._hours = 24

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        hd = QLabel("RIWAYAT SENSOR")
        hd.setStyleSheet(f"color:{T.FG1};font-size:13px;font-weight:bold;letter-spacing:2px;")
        hd.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hd)

        # Baris filter: parameter + rentang waktu
        fr = QHBoxLayout(); fr.setSpacing(4)
        self._param_btns = {}
        for name in config.ALL_SENSORS:
            b = QPushButton(name)
            b.setCheckable(True)
            b.setProperty("selector", "true")
            b.setChecked(name == self._param)
            b.clicked.connect(lambda _, n=name: self._pick_param(n))
            fr.addWidget(b)
            self._param_btns[name] = b
        fr.addStretch()
        self._range_btns = {}
        for label, hours in self._RANGES:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setProperty("selector", "true")
            b.setChecked(hours == self._hours)
            b.clicked.connect(lambda _, hh=hours: self._pick_range(hh))
            fr.addWidget(b)
            self._range_btns[hours] = b
        root.addLayout(fr)

        self._chart = HistoryChart()
        root.addWidget(self._chart, 1)

        # Stat tiles: MIN / RATA-RATA / MAKS / TERAKHIR
        st = QHBoxLayout(); st.setSpacing(24)
        st.addStretch()
        self._stats = {}
        for key in ("MIN", "RATA-RATA", "MAKS", "TERAKHIR"):
            col = QVBoxLayout(); col.setSpacing(0)
            lb = QLabel(key)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setStyleSheet(f"color:{T.FG3};font-size:8px;letter-spacing:1px;")
            lv = QLabel("–")
            lv.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lv.setStyleSheet(
                f"color:{T.FG1};font-size:15px;font-weight:bold;font-family:'{T.MONO}';")
            col.addWidget(lb); col.addWidget(lv)
            st.addLayout(col)
            self._stats[key] = lv
        st.addStretch()
        root.addLayout(st)

        bl = QHBoxLayout(); bl.addStretch()
        bc = QPushButton("Tutup"); bc.clicked.connect(self.reject)
        bl.addWidget(bc)
        root.addLayout(bl)

        self._reload()

    def _pick_param(self, name: str):
        self._param = name
        for n, b in self._param_btns.items():
            b.setChecked(n == name)
        self._reload()

    def _pick_range(self, hours: int):
        self._hours = hours
        for hh, b in self._range_btns.items():
            b.setChecked(hh == hours)
        self._reload()

    def _reload(self):
        rows = self._hist.recent(hours=self._hours)
        idx = self._COL[self._param]
        data = [(r[0], r[idx]) for r in rows if r[idx] is not None]
        color, unit, lo, hi = T.SENSORS[self._param]
        self._chart.set_data(data, color, unit, hi)

        if data:
            vals = [v for _, v in data]
            self._stats["MIN"].setText(f"{min(vals):.2f}")
            self._stats["RATA-RATA"].setText(f"{sum(vals)/len(vals):.2f}")
            self._stats["MAKS"].setText(f"{max(vals):.2f}")
            self._stats["TERAKHIR"].setText(f"{vals[-1]:.2f}")
        else:
            for lv in self._stats.values():
                lv.setText("–")

# ── Main Window ───────────────────────────────────────────────

class MainWindow(QMainWindow):

    _STATUS_META = {
        OperationalStatus.NORMAL:      ("NORMAL",         T.OK,   None),
        OperationalStatus.STOPPED:     ("-1  BERHENTI",   T.WARN, T.WARN),
        OperationalStatus.CALIBRATION: ("-2  KALIBRASI",  T.BLUE, T.BLUE),
        OperationalStatus.MALFUNCTION: ("-3  ALAT RUSAK", T.ERR,  T.ERR),
    }
    _BANNER_TEXT = {
        OperationalStatus.STOPPED:
            "PRODUKSI BERHENTI  —  RTU mengirim nilai  -1  ke server",
        OperationalStatus.CALIBRATION:
            "KALIBRASI / AUDIT AKTIF  —  RTU mengirim nilai  -2  ke server",
        OperationalStatus.MALFUNCTION:
            "ALAT RUSAK / TIDAK OPTIMAL  —  RTU mengirim nilai  -3  ke server",
    }

    def __init__(self):
        super().__init__()
        self.signal_bridge = SignalBridge()
        self.setWindowTitle("SPARING – IPAL Monitoring")
        self.resize(1024, 600)
        self.setStyleSheet(f"QMainWindow{{background:{T.BG};}}")

        self._total_readings = 0
        self._ok_readings    = 0
        self._real           = False   # False = masih simulasi
        self._t0             = datetime.now()
        self._tick_n         = 0

        self.signal_bridge.sensor_update.connect(self._on_sensor)
        self.signal_bridge.connection_update.connect(self._on_conn)
        self.signal_bridge.notification.connect(self._show_notification)
        self.signal_bridge.data_count_update.connect(self._on_count)
        self.signal_bridge.daily_data_update.connect(self._on_daily)
        self.signal_bridge.status_update.connect(self._on_status_update)
        self.signal_bridge.log_entry.connect(self._on_log_entry)
        self.signal_bridge.secret_key_update.connect(self._on_secret_key)
        self.signal_bridge.modbus_log.connect(self._on_modbus_log)
        self.signal_bridge.server_status_update.connect(self._on_server_status)
        self.signal_bridge.backup_count_update.connect(self._on_backup_count)

        cw = QWidget()
        cw.setStyleSheet(f"background:{T.BG};")
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._mk_header(root)
        self._mk_status_banner(root)
        self._mk_sim_banner(root)   # banner simulasi — fix baru

        body = QWidget()
        body.setStyleSheet(f"background:{T.BG};")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(8, 8, 8, 6)
        bl.setSpacing(8)
        self._mk_cards(bl)
        self._mk_sidebar(bl)
        root.addWidget(body, 1)

        self._mk_notif_bar(root)
        self._mk_footer(root)

        # Simulasi — dihentikan saat data nyata pertama masuk
        self._sim_t = QTimer()
        self._sim_t.timeout.connect(self._simulate)
        self._sim_t.start(2000)

    # ─────────────────────────────────────── Header ──

    def _mk_header(self, root):
        hdr = QFrame()
        hdr.setFixedHeight(42)
        hdr.setStyleSheet(
            f"QFrame{{background:{T.PANEL};border-bottom:1px solid {T.BORDER};}}"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 12, 0)
        hl.setSpacing(10)

        # Brand — amber accent bar + teks, tanpa emoji
        brand = QWidget(); brand.setStyleSheet("background:transparent;")
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(0,0,0,0); bl.setSpacing(6)
        accent_bar = QFrame()
        accent_bar.setFixedSize(3, 20)
        accent_bar.setStyleSheet(f"background:{T.AMBER};border:none;")
        logo = QLabel("MITRA MUTIARA")
        logo.setStyleSheet(
            f"color:{T.FG1};font-size:12px;font-weight:bold;"
            f"letter-spacing:2px;font-family:'{T.FONT}';"
        )
        sub = QLabel("SPARING v5")
        sub.setStyleSheet(f"color:{T.FG3};font-size:9px;font-family:'{T.MONO}';")
        bl.addWidget(accent_bar); bl.addWidget(logo); bl.addWidget(sub)
        hl.addWidget(brand)

        self._h_status = QLabel("ONLINE")
        self._h_status.setFixedWidth(58)
        self._h_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._h_status.setStyleSheet(
            f"color:{T.OK};font-size:9px;font-weight:bold;"
            f"font-family:'{T.MONO}';"
            f"border:1px solid {T.OK};border-radius:2px;"
            f"background:{T.rgba(T.OK,18)};padding:2px 4px;"
        )
        hl.addWidget(self._h_status)
        hl.addStretch()

        self._h_time = QLabel("--:--:--")
        self._h_time.setStyleSheet(
            f"color:{T.FG1};font-size:22px;font-weight:bold;"
            f"font-family:'{T.MONO}';"
        )
        hl.addWidget(self._h_time)

        self._h_date = QLabel(datetime.now().strftime("%d %b %Y"))
        self._h_date.setStyleSheet(f"color:{T.FG3};font-size:10px;")
        hl.addWidget(self._h_date)
        hl.addStretch()

        def action_btn(text, color=T.FG2):
            lb = QLabel(text)
            lb.setStyleSheet(
                f"color:{color};border:1px solid {T.rgba(color,100)};"
                f"border-radius:2px;padding:3px 10px;"
                f"font-size:9px;font-weight:bold;font-family:'{T.FONT}';"
                f"letter-spacing:0.5px;"
            )
            lb.setCursor(Qt.CursorShape.PointingHandCursor)
            return lb

        self._fs_btn = action_btn("JENDELA", T.FG2)
        self._fs_btn.mousePressEvent = self._toggle_fullscreen
        hl.addWidget(self._fs_btn)

        hist_btn = action_btn("RIWAYAT", T.BLUE)
        hist_btn.mousePressEvent = lambda _: HistoryDialog(self).exec()
        hl.addWidget(hist_btn)

        cfg_btn = action_btn("PENGATURAN", T.AMBER)
        cfg_btn.mousePressEvent = lambda _: SettingsDialog(self).exec()
        hl.addWidget(cfg_btn)

        hl.addSpacing(4)

        def info_tag(text):
            lb = QLabel(text)
            lb.setStyleSheet(
                f"color:{T.FG3};border:1px solid {T.BORDER};"
                f"border-radius:2px;padding:2px 7px;font-size:9px;"
                f"font-family:'{T.MONO}';"
            )
            return lb

        self._chip_ip   = info_tag(f"IP {get_ip()}")
        self._chip_cpu  = info_tag("CPU --")
        self._chip_ram  = info_tag("RAM --")
        self._chip_volt = info_tag("VOLT --")   # pindahan dari seksi Sistem RPi
        for c in (self._chip_ip, self._chip_cpu, self._chip_ram, self._chip_volt):
            hl.addWidget(c)

        root.addWidget(hdr)

        self._clk = QTimer()
        self._clk.timeout.connect(self._tick)
        self._clk.start(1000)

    def _toggle_fullscreen(self, _=None):
        if self.isFullScreen():
            self.showNormal()
            self._fs_btn.setText("LAYAR PENUH")
        else:
            self.showFullScreen()
            self._fs_btn.setText("JENDELA")

    def _tick(self):
        self._h_time.setText(datetime.now().strftime("%H:%M:%S"))
        self._tick_n += 1
        if self._tick_n % 5 == 0:
            temp = get_cpu_temp()
            try:
                tv  = float(temp.replace("C", ""))
                col = T.ERR if tv > 70 else (T.WARN if tv > 55 else T.FG3)
            except ValueError:
                col = T.FG3
            self._chip_cpu.setText(f"CPU {temp}")
            self._chip_cpu.setStyleSheet(
                f"color:{col};border:1px solid {T.rgba(col,80)};"
                f"border-radius:2px;padding:2px 7px;font-size:9px;"
                f"font-family:'{T.MONO}';"
            )
            self._chip_ram.setText(f"RAM {get_mem()}")
            # Voltage — sekarang di header, bukan di sidebar section
            volt = get_rpi_voltage()
            self._chip_volt.setText(f"VOLT {volt}")
            # Tanggal ikut diperbarui — berubah saat lewat tengah malam
            self._h_date.setText(datetime.now().strftime("%d %b %Y"))
        if self._tick_n % 30 == 0:
            # IP bisa berubah saat WiFi reconnect / ganti jaringan
            self._chip_ip.setText(f"IP {get_ip()}")

    # ─────────────────────────────────────── Status Banner ──

    def _mk_status_banner(self, root):
        self._banner = QFrame()
        self._banner.setFixedHeight(24)
        self._banner.setVisible(False)
        self._banner_lbl = QLabel()
        self._banner_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl = QHBoxLayout(self._banner)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self._banner_lbl)
        root.addWidget(self._banner)

    # ─────────────────────────────────────── Simulation Banner ──

    def _mk_sim_banner(self, root):
        """
        Ditampilkan saat aplikasi pertama dibuka (data belum dari sensor nyata).
        Dihilangkan otomatis ketika sinyal sensor nyata pertama masuk.
        Fix: operator tahu mana data simulasi vs data nyata.
        """
        self._sim_banner = QFrame()
        self._sim_banner.setFixedHeight(20)
        self._sim_banner.setStyleSheet(
            f"background:{T.rgba(T.AMBER, 18)};"
            f"border-bottom:1px solid {T.rgba(T.AMBER, 60)};"
        )
        lbl = QLabel("MODE SIMULASI  —  Menunggu koneksi sensor nyata...")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color:{T.AMBER};font-size:9px;font-weight:bold;"
            f"font-family:'{T.MONO}';letter-spacing:0.5px;"
        )
        bl = QHBoxLayout(self._sim_banner)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(lbl)
        root.addWidget(self._sim_banner)

    # ─────────────────────────────────────── Notification Bar ──

    def _mk_notif_bar(self, root):
        self._notif_bar = QFrame()
        self._notif_bar.setFixedHeight(22)
        self._notif_bar.setVisible(False)
        self._notif_lbl = QLabel()
        self._notif_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nl = QHBoxLayout(self._notif_bar)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.addWidget(self._notif_lbl)
        root.addWidget(self._notif_bar)

        self._notif_timer = QTimer()
        self._notif_timer.setSingleShot(True)
        self._notif_timer.timeout.connect(lambda: self._notif_bar.setVisible(False))

    def _show_notification(self, msg: str, duration: int):
        msg_lo = msg.lower()
        if any(w in msg_lo for w in ("berhasil", "terhubung", "tersambung",
                                     "diambil", "dipulihkan", "tersinkronisasi")):
            color = T.OK
        elif any(w in msg_lo for w in ("gagal", "rusak", "error")):
            color = T.ERR
        else:
            color = T.AMBER
        self._notif_bar.setStyleSheet(
            f"background:{T.rgba(color, 25)};"
            f"border-top:1px solid {T.rgba(color, 100)};"
        )
        self._notif_lbl.setText(msg.upper())
        self._notif_lbl.setStyleSheet(
            f"color:{color};font-weight:bold;font-size:9px;"
            f"font-family:'{T.MONO}';letter-spacing:0.5px;"
        )
        self._notif_bar.setVisible(True)
        self._notif_timer.start(duration)

    # ─────────────────────────────────────── Cards ──

    def _mk_cards(self, parent):
        cw = QWidget(); cw.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(cw)
        hl.setSpacing(6); hl.setContentsMargins(0, 0, 0, 0)
        self.cards = {}
        for name, (color, unit, lo, hi) in T.SENSORS.items():
            card = SensorCard(name, color, unit, lo, hi)
            card.setVisible(name in config.display_sensors)
            hl.addWidget(card)
            self.cards[name] = card
        parent.addWidget(cw, 1)

    # ─────────────────────────────────────── Sidebar ──

    def _mk_sidebar(self, parent):
        sc = QWidget(); sc.setStyleSheet("background:transparent;")
        vb = QVBoxLayout(sc)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(5)

        # ── 1. STATUS OPERASIONAL ──
        s_op = Section("Status Operasional", color=T.WARN)
        self._op_status_lbl = s_op.row("Status", "NORMAL", T.OK)

        # Tombol "UBAH STATUS" — toggle panel tersembunyi
        # Fix: tombol kondisi tidak selalu terlihat → cegah press tidak sengaja
        self._btn_ubah = QPushButton("UBAH STATUS")
        self._btn_ubah.setFixedHeight(30)
        self._btn_ubah.setStyleSheet(
            f"QPushButton{{background:{T.rgba(T.WARN,28)};color:{T.WARN};"
            f"border:1px solid {T.rgba(T.WARN,100)};border-radius:2px;"
            f"font-size:9px;font-weight:bold;font-family:'{T.MONO}';}}"
            f"QPushButton:hover{{background:{T.rgba(T.WARN,60)};}}"
        )
        self._btn_ubah.clicked.connect(self._toggle_op_panel)
        s_op.add(self._btn_ubah)

        # Panel tombol kondisi — tersembunyi secara default
        self._op_panel = QWidget()
        self._op_panel.setVisible(False)
        self._op_panel.setStyleSheet("background:transparent;")
        op_pvb = QVBoxLayout(self._op_panel)
        op_pvb.setContentsMargins(0, 4, 0, 0); op_pvb.setSpacing(4)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)

        def _op_btn(label, color):
            b = QPushButton(label)
            b.setFixedHeight(44)
            b.setStyleSheet(self._op_btn_style(color, active=False))
            return b

        self._btn_m1 = _op_btn("-1\nBERHENTI",  T.WARN)
        self._btn_m2 = _op_btn("-2\nKALIBRASI", T.BLUE)
        self._btn_m3 = _op_btn("-3\nRUSAK",      T.ERR)

        self._btn_m1.clicked.connect(lambda: self._set_status(OperationalStatus.STOPPED))
        self._btn_m2.clicked.connect(lambda: self._set_status(OperationalStatus.CALIBRATION))
        self._btn_m3.clicked.connect(lambda: self._set_status(OperationalStatus.MALFUNCTION))

        btn_row.addWidget(self._btn_m1)
        btn_row.addWidget(self._btn_m2)
        btn_row.addWidget(self._btn_m3)
        op_pvb.addLayout(btn_row)

        self._btn_reset_op = QPushButton("KEMBALI KE NORMAL")
        self._btn_reset_op.setFixedHeight(30)
        self._btn_reset_op.setStyleSheet(
            f"QPushButton{{background:{T.rgba(T.OK,28)};color:{T.OK};"
            f"border:1px solid {T.OK};border-radius:2px;"
            f"font-size:9px;font-weight:bold;font-family:'{T.MONO}';}}"
            f"QPushButton:hover{{background:{T.rgba(T.OK,60)};}}"
        )
        self._btn_reset_op.setVisible(False)
        self._btn_reset_op.clicked.connect(lambda: self._set_status(OperationalStatus.NORMAL))
        op_pvb.addWidget(self._btn_reset_op)

        s_op.add(self._op_panel)
        vb.addWidget(s_op)

        # ── 2. ALARM ──
        self._alarm_frame = QFrame()
        self._alarm_frame.setStyleSheet(
            f"background:{T.rgba(T.OK,18)};border:1px solid {T.rgba(T.OK,80)};border-radius:2px;"
        )
        af = QVBoxLayout(self._alarm_frame)
        af.setContentsMargins(8, 5, 8, 5); af.setSpacing(2)
        self._alarm_title = QLabel("OK  —  Tidak ada alarm")
        self._alarm_title.setStyleSheet(
            f"color:{T.OK};font-weight:bold;font-size:9px;"
            f"font-family:'{T.MONO}';letter-spacing:0.5px;"
        )
        self._alarm_desc = QLabel("Semua parameter dalam batas normal.")
        self._alarm_desc.setWordWrap(True)
        self._alarm_desc.setStyleSheet(f"color:{T.FG2};font-size:9px;")
        af.addWidget(self._alarm_title)
        af.addWidget(self._alarm_desc)
        vb.addWidget(self._alarm_frame)

        # ── 3. DATA BUFFER ──
        s_buf = Section("Data Buffer", color=T.BLUE)
        self._buf_lbl = s_buf.row("Tersimpan", "0 data")
        self._buf_backup = s_buf.row("Backup Pending", "0")
        bar = QProgressBar()
        bar.setFixedHeight(4); bar.setValue(0)
        bar.setStyleSheet(
            f"QProgressBar{{background:{T.BORDER};border:none;border-radius:1px;}}"
            f"QProgressBar::chunk{{background:{T.AMBER};border-radius:1px;}}"
        )
        self._buf_bar = bar
        s_buf.add(bar)
        vb.addWidget(s_buf)

        # ── 4. STATUS SERVER ──
        # Baris status = hasil KIRIM terakhir per server (bukan cek internet)
        s_sv = Section("Status Server", color=T.BLUE)
        self._mm_conn = s_sv.row("Mitra Mutiara", "MENUNGGU", T.FG2)
        self._kl_conn = s_sv.row("KLHK", "MENUNGGU", T.FG2)
        self._kl_uid  = s_sv.row("UID KLHK", config.server.uid_2)
        self._mm_key  = s_sv.row("Key MM", "–", T.FG3)
        self._kl_key  = s_sv.row("Key KLHK", "–", T.FG3)
        vb.addWidget(s_sv)

        # ── 6. MODBUS RS485 ──
        s_mb = Section("Modbus RS485 (USB)")
        self._mb_port = s_mb.row("Port", config.modbus.port)
        self._mb_last = s_mb.row("Terakhir Dibaca", "--:--:--")
        self._mb_stat = s_mb.row("Status", "MENUNGGU", T.WARN)

        led_row = QHBoxLayout(); led_row.setSpacing(3)
        self._leds = {}
        for key in ("PH", "TSS", "FLW", "COD", "NH3"):
            lb = QLabel(key)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setFixedWidth(34); lb.setFixedHeight(20)
            lb.setStyleSheet(
                f"background:{T.rgba(T.OFF,20)};color:{T.OFF};"
                f"border:1px solid {T.rgba(T.OFF,60)};border-radius:2px;"
                f"font-size:8px;font-family:'{T.MONO}';font-weight:bold;"
            )
            led_row.addWidget(lb)
            self._leds[key] = lb
        s_mb._vb.addLayout(led_row)

        self._mb_log_labels: list = []
        for _ in range(4):
            lbl = QLabel("–")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color:{T.FG3};font-size:9px;font-family:'{T.MONO}';"
            )
            s_mb.add(lbl)
            self._mb_log_labels.append(lbl)
        self._mb_log_entries: list = []
        vb.addWidget(s_mb)

        # ── 7. FAKTOR KALIBRASI ──
        s_off = Section("Faktor Kalibrasi")
        self._off_ph  = s_off.row("pH",    f"{config.offsets.ph_factor:.3f} ×")
        self._off_tss = s_off.row("TSS",   f"{config.offsets.tss_factor:.3f} ×")
        self._off_dbt = s_off.row("Debit", f"{config.offsets.debit_factor:.3f} ×")
        vb.addWidget(s_off)

        # Sistem RPi (voltage) dipindahkan ke header chip — tidak ada seksi terpisah

        # ── LOG PENGIRIMAN ──
        s_log = Section("Log Pengiriman", color=T.BLUE)
        self._log_labels: list = []
        for _ in range(5):
            lbl = QLabel("–")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color:{T.FG3};font-size:9px;"
                f"font-family:'{T.MONO}';"
            )
            s_log.add(lbl)
            self._log_labels.append(lbl)
        self._log_entries: list = []
        vb.addWidget(s_log)

        vb.addStretch()

        # Sidebar lebar tetap 228px — kartu sensor mendapat ruang lebih
        sa = QScrollArea()
        sa.setWidget(sc); sa.setWidgetResizable(True)
        sa.setFixedWidth(228)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setStyleSheet("""
            QScrollArea{border:none;background:transparent;}
            QScrollArea>QWidget>QWidget{background:transparent;}
            QScrollBar:vertical{border:none;background:#111318;width:4px;}
            QScrollBar::handle:vertical{background:#2d3252;min-height:20px;border-radius:2px;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}
        """)
        parent.addWidget(sa)

    def _toggle_op_panel(self):
        vis = not self._op_panel.isVisible()
        self._op_panel.setVisible(vis)
        self._btn_ubah.setText("TUTUP" if vis else "UBAH STATUS")

    @staticmethod
    def _op_btn_style(color: str, active: bool) -> str:
        if active:
            return (
                f"QPushButton{{background:{color};color:#000;"
                f"border:2px solid {color};border-radius:2px;"
                f"font-size:9px;font-weight:bold;font-family:'{T.MONO}';}}"
                f"QPushButton:hover{{background:{color};}}"
            )
        return (
            f"QPushButton{{background:{T.rgba(color,28)};color:{color};"
            f"border:1px solid {T.rgba(color,100)};border-radius:2px;"
            f"font-size:9px;font-weight:bold;font-family:'{T.MONO}';}}"
            f"QPushButton:hover{{background:{T.rgba(color,60)};}}"
        )

    # ─────────────────────────────────────── Footer ──

    def _mk_footer(self, root):
        ft = QFrame()
        ft.setFixedHeight(48)
        ft.setStyleSheet(
            f"QFrame{{background:{T.PANEL};border-top:1px solid {T.BORDER};}}"
        )
        hl = QHBoxLayout(ft)
        hl.setContentsMargins(12, 0, 12, 0); hl.setSpacing(0)

        left = QWidget(); left.setStyleSheet("background:transparent;")
        ll = QVBoxLayout(left)
        ll.setSpacing(1); ll.setContentsMargins(0,0,0,0)
        ll.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        port_lbl = QLabel(f"USB  {config.modbus.port}")
        port_lbl.setStyleSheet(
            f"color:{T.FG2};font-size:10px;font-weight:bold;font-family:'{T.MONO}';"
        )
        baud_lbl = QLabel(f"Baud {config.modbus.baudrate}  |  SPARING RTU v5.0")
        baud_lbl.setStyleSheet(
            f"color:{T.FG3};font-size:9px;font-family:'{T.MONO}';"
        )
        ll.addWidget(port_lbl); ll.addWidget(baud_lbl)
        hl.addWidget(left)

        hl.addStretch()

        def stat_block(label, init_val, color=T.FG1):
            w = QWidget(); w.setStyleSheet("background:transparent;")
            vb = QVBoxLayout(w)
            vb.setSpacing(0); vb.setContentsMargins(16,4,16,4)
            vb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb = QLabel(label.upper())
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setStyleSheet(
                f"color:{T.FG3};font-size:8px;letter-spacing:1px;font-family:'{T.FONT}';"
            )
            lv = QLabel(init_val)
            lv.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lv.setStyleSheet(
                f"color:{color};font-weight:bold;font-size:16px;"
                f"font-family:'{T.MONO}';"
            )
            vb.addWidget(lb); vb.addWidget(lv)
            return w, lv

        def vsep():
            c = QWidget(); c.setStyleSheet("background:transparent;")
            ch = QHBoxLayout(c); ch.setContentsMargins(0,8,0,8)
            line = QFrame(); line.setFrameShape(QFrame.Shape.VLine)
            line.setFixedWidth(1)
            line.setStyleSheet(f"background:{T.BORDER};border:none;")
            ch.addWidget(line)
            return c

        u_blk, self._f_uptime     = stat_block("Uptime",    "0j 0m",  T.OK)
        s_blk, self._f_sent       = stat_block("Terkirim",  "0 data", T.BLUE)
        a_blk, self._f_alarms     = stat_block("Alarm",     "0",      T.FG1)
        k_blk, self._f_compliance = stat_block("Kepatuhan", "--",     T.FG2)

        for i, w in enumerate([u_blk, s_blk, a_blk, k_blk]):
            if i > 0: hl.addWidget(vsep())
            hl.addWidget(w)

        hl.addStretch()

        right = QWidget(); right.setStyleSheet("background:transparent;")
        rl = QVBoxLayout(right)
        rl.setSpacing(2); rl.setContentsMargins(0,0,0,0)
        rl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self._ft_conn = QLabel("ONLINE")
        self._ft_conn.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._ft_conn.setStyleSheet(
            f"color:{T.OK};font-size:9px;font-weight:bold;font-family:'{T.MONO}';"
        )
        brand = QLabel("Mitra Mutiara Internasional")
        brand.setAlignment(Qt.AlignmentFlag.AlignRight)
        brand.setStyleSheet(f"color:{T.FG3};font-size:9px;")
        rl.addWidget(self._ft_conn); rl.addWidget(brand)
        hl.addWidget(right)

        root.addWidget(ft)

        self._upt = QTimer()
        self._upt.timeout.connect(self._uptime)
        self._upt.start(60000)

    def _uptime(self):
        d = datetime.now() - self._t0
        h, m = d.seconds // 3600, (d.seconds % 3600) // 60
        self._f_uptime.setText(f"{d.days}h {h}j {m}m" if d.days else f"{h}j {m}m")

    # ─────────────────────────────────────── Slots ──

    def _simulate(self):
        # Timer ini dihentikan saat data nyata pertama masuk (_on_sensor)
        self.cards["pH"].update_value(random.uniform(6.8, 7.5))
        self.cards["TSS"].update_value(random.uniform(45, 65))
        self.cards["DEBIT"].update_value(random.uniform(85, 115))
        self.cards["COD"].update_value(random.uniform(80, 120))
        self.cards["NH3-N"].update_value(random.uniform(2, 5))

    def _on_sensor(self, data: SensorData):
        # Pertama kali data nyata masuk: hentikan simulasi dan sembunyikan banner
        if not self._real:
            self._real = True
            self._sim_t.stop()           # fix: timer tidak dibiarkan jalan terus
            self._sim_banner.setVisible(False)

        ts = data.timestamp
        self.cards["pH"].update_value(data.ph, ts)
        self.cards["TSS"].update_value(data.tss, ts)
        self.cards["DEBIT"].update_value(data.debit, ts)
        self.cards["COD"].update_value(data.cod, ts)
        self.cards["NH3-N"].update_value(data.nh3n, ts)

        # LED per sensor: hijau = OK, merah = gagal, abu-abu = tidak ada sensor
        led_state = {
            "PH":  data.ph_ok,  "TSS": data.tss_ok, "FLW": data.debit_ok,
            "COD": data.cod_ok, "NH3": data.nh3n_ok,
        }
        for key, lb in self._leds.items():
            ok = led_state.get(key)
            c = T.OK if ok else (T.OFF if ok is None else T.ERR)
            lb.setStyleSheet(
                f"background:{T.rgba(c,22)};color:{c};"
                f"border:1px solid {T.rgba(c,80)};border-radius:2px;"
                f"font-size:8px;font-family:'{T.MONO}';font-weight:bold;"
            )

        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        self._mb_last.setText(ts_str)
        flags = [v for v in (data.ph_ok, data.tss_ok, data.debit_ok,
                             data.cod_ok, data.nh3n_ok) if v is not None]
        ok_n, n = sum(1 for v in flags if v), max(len(flags), 1)
        if ok_n == 0:
            self._mb_stat.setText("GAGAL")
            stat_col = T.ERR
        else:
            self._mb_stat.setText(f"AKTIF {ok_n}/{n}")
            stat_col = T.OK if ok_n == n else T.WARN
        self._mb_stat.setStyleSheet(
            f"color:{stat_col};font-size:11px;font-weight:bold;font-family:'{T.MONO}';"
        )
        self._check_alarms(data)

    def _on_conn(self, ok: bool):
        # Hanya status internet (header + footer) — status per server
        # diperbarui dari hasil kirim nyata via _on_server_status
        col = T.OK if ok else T.OFF
        st = "ONLINE" if ok else "OFFLINE"
        self._ft_conn.setText(st)
        self._ft_conn.setStyleSheet(
            f"color:{col};font-size:9px;font-weight:bold;font-family:'{T.MONO}';"
        )
        self._h_status.setText(st)
        self._h_status.setStyleSheet(
            f"color:{col};font-size:9px;font-weight:bold;font-family:'{T.MONO}';"
            f"border:1px solid {col};border-radius:2px;"
            f"background:{T.rgba(col,18)};padding:2px 4px;"
        )

    def _on_server_status(self, ok1: bool, ok2: bool):
        """Update status per server berdasarkan hasil kirim nyata."""
        now = datetime.now().strftime("%H:%M")
        for lb, ok in ((self._mm_conn, ok1), (self._kl_conn, ok2)):
            lb.setText(f"OK {now}" if ok else f"GAGAL {now}")
            col = T.OK if ok else T.ERR
            lb.setStyleSheet(
                f"color:{col};font-size:11px;font-weight:bold;font-family:'{T.MONO}';"
            )

    def _on_count(self, cur, mx):
        self._buf_lbl.setText(f"{cur} data")
        self._buf_bar.setValue(int(cur / mx * 100) if mx else 0)

    def _on_backup_count(self, n: int):
        self._buf_backup.setText(str(n))
        col = T.WARN if n > 0 else T.FG1
        self._buf_backup.setStyleSheet(
            f"color:{col};font-size:11px;font-weight:bold;font-family:'{T.MONO}';"
        )

    def _on_daily(self, n):
        self._f_sent.setText(f"{n} data")

    def _on_log_entry(self, entry: str):
        """Tambah entri log pengiriman ke sidebar (maks 5, terbaru di atas)."""
        self._log_entries.insert(0, entry)
        if len(self._log_entries) > 5:
            self._log_entries.pop()
        for i, lbl in enumerate(self._log_labels):
            if i < len(self._log_entries):
                text = self._log_entries[i]
                # Warnai merah jika GAGAL
                col = T.ERR if "GAGAL" in text else T.OK
                lbl.setText(text)
                lbl.setStyleSheet(
                    f"color:{col};font-size:9px;"
                    f"font-family:'{T.MONO}';"
                )
            else:
                lbl.setText("–")
                lbl.setStyleSheet(
                    f"color:{T.FG3};font-size:9px;"
                    f"font-family:'{T.MONO}';"
                )

    def _on_modbus_log(self, entry: str):
        """Tambah entri log modbus ke sidebar (maks 4, terbaru di atas)."""
        self._mb_log_entries.insert(0, entry)
        if len(self._mb_log_entries) > 4:
            self._mb_log_entries.pop()
        for i, lbl in enumerate(self._mb_log_labels):
            if i < len(self._mb_log_entries):
                text = self._mb_log_entries[i]
                col = T.ERR if any(k in text for k in ("GAGAL", "ERROR", "ANOMALI")) else T.FG3
                lbl.setText(text)
                lbl.setStyleSheet(
                    f"color:{col};font-size:9px;font-family:'{T.MONO}';"
                )
            else:
                lbl.setText("–")
                lbl.setStyleSheet(
                    f"color:{T.FG3};font-size:9px;font-family:'{T.MONO}';"
                )

    def _on_secret_key(self, preview1: str, preview2: str):
        """Tampilkan preview secret key di sidebar setelah berhasil diambil."""
        def _set(lbl, preview):
            lbl.setText(preview if preview else "–")
            col = T.OK if preview and preview != "–" else T.FG3
            lbl.setStyleSheet(
                f"color:{col};font-size:11px;font-weight:bold;font-family:'{T.MONO}';"
            )
        _set(self._mm_key, preview1)
        _set(self._kl_key, preview2)

    def _check_alarms(self, d: SensorData):
        msgs = []
        if not (6 <= d.ph <= 9):   msgs.append(f"pH {d.ph:.2f} (batas 6-9)")
        if d.tss  > 100:           msgs.append(f"TSS {d.tss:.1f} > 100 mg/L")
        if d.cod  > 300:           msgs.append(f"COD {d.cod:.1f} > 300 mg/L")
        if d.nh3n > 10:            msgs.append(f"NH3-N {d.nh3n:.1f} > 10 mg/L")
        n = len(msgs)

        self._total_readings += 1
        if n == 0: self._ok_readings += 1
        pct = self._ok_readings / self._total_readings * 100
        pct_col = T.OK if pct >= 90 else (T.WARN if pct >= 70 else T.ERR)
        self._f_compliance.setText(f"{pct:.1f}%")
        self._f_compliance.setStyleSheet(
            f"color:{pct_col};font-weight:bold;font-size:16px;font-family:'{T.MONO}';"
        )

        self._f_alarms.setText(str(n))
        if n:
            self._f_alarms.setStyleSheet(
                f"color:{T.ERR};font-weight:bold;font-size:16px;font-family:'{T.MONO}';"
            )
            self._alarm_frame.setStyleSheet(
                f"background:{T.rgba(T.ERR,18)};border:1px solid {T.rgba(T.ERR,80)};border-radius:2px;"
            )
            self._alarm_title.setText(f"ALARM  —  {n} parameter")
            self._alarm_title.setStyleSheet(
                f"color:{T.ERR};font-weight:bold;font-size:9px;"
                f"font-family:'{T.MONO}';letter-spacing:0.5px;"
            )
            self._alarm_desc.setText("  |  ".join(msgs))
        else:
            self._f_alarms.setStyleSheet(
                f"color:{T.FG1};font-weight:bold;font-size:16px;font-family:'{T.MONO}';"
            )
            self._alarm_frame.setStyleSheet(
                f"background:{T.rgba(T.OK,18)};border:1px solid {T.rgba(T.OK,80)};border-radius:2px;"
            )
            self._alarm_title.setText("OK  —  Tidak ada alarm")
            self._alarm_title.setStyleSheet(
                f"color:{T.OK};font-weight:bold;font-size:9px;"
                f"font-family:'{T.MONO}';letter-spacing:0.5px;"
            )
            self._alarm_desc.setText("Semua parameter dalam batas normal.")

    # ─────────────────────────────────────── Status Operasional ──

    def _set_status(self, status: OperationalStatus, confirm: bool = True):
        # confirm=False dipakai saat restore status dari file (startup) —
        # tanpa dialog konfirmasi karena bukan aksi operator
        if status != OperationalStatus.NORMAL and confirm:
            label, color, _ = self._STATUS_META[status]
            reply = QMessageBox.warning(
                self,
                "Konfirmasi Status Operasional",
                f"Ubah status ke:\n\n  {label}\n\n"
                f"Semua data yang dikirim ke server akan menggunakan\n"
                f"kode kondisi  {int(status)}  sampai status direset.\n\n"
                f"Lanjutkan?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        OperationalState.set(status)
        label, color, banner_color = self._STATUS_META[status]

        # Tutup panel setelah status berhasil diubah
        self._op_panel.setVisible(False)
        self._btn_ubah.setText("UBAH STATUS")

        self._op_status_lbl.setText(label)
        self._op_status_lbl.setStyleSheet(
            f"color:{color};font-size:11px;font-weight:bold;font-family:'{T.MONO}';"
        )
        self._btn_reset_op.setVisible(status != OperationalStatus.NORMAL)
        self._update_status_buttons(status)

        if status == OperationalStatus.NORMAL:
            self._banner.setVisible(False)
        else:
            self._banner.setVisible(True)
            self._banner.setStyleSheet(
                f"background:{T.rgba(banner_color,35)};"
                f"border-bottom:1px solid {T.rgba(banner_color,120)};"
            )
            self._banner_lbl.setText(self._BANNER_TEXT[status])
            self._banner_lbl.setStyleSheet(
                f"color:{banner_color};font-weight:bold;font-size:10px;"
                f"font-family:'{T.MONO}';letter-spacing:1px;"
            )

        print(f"[STATUS] Operasional: {label}")

    def _update_status_buttons(self, status: OperationalStatus):
        mapping = [
            (self._btn_m1, OperationalStatus.STOPPED,     T.WARN),
            (self._btn_m2, OperationalStatus.CALIBRATION, T.BLUE),
            (self._btn_m3, OperationalStatus.MALFUNCTION, T.ERR),
        ]
        for btn, op_status, color in mapping:
            btn.setStyleSheet(self._op_btn_style(color, active=(status == op_status)))

    def _on_status_update(self, value: int):
        self._set_status(OperationalStatus(value))

    # ─────────────────────────────────────── Refresh ──

    def refresh_sidebar(self):
        self._kl_uid.setText(config.server.uid_2)
        self._off_ph.setText(f"{config.offsets.ph_factor:.3f} ×")
        self._off_tss.setText(f"{config.offsets.tss_factor:.3f} ×")
        self._off_dbt.setText(f"{config.offsets.debit_factor:.3f} ×")
        self.refresh_cards()

    def refresh_cards(self):
        for name, card in self.cards.items():
            card.setVisible(name in config.display_sensors)

    def update_gpio_status(self, gpio_ok: bool):
        pass

# ── Factory ───────────────────────────────────────────────────

def create_application():
    app = QApplication(sys.argv)
    app.setFont(QFont(T.FONT))
    window = MainWindow()
    return app, window

if __name__ == "__main__":
    app, window = create_application()
    window.showFullScreen()
    sys.exit(app.exec())
