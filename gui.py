"""
SPARING Display GUI
Industrial Dark Theme - Optimized for 7" 1024x600 display
Target: Raspberry Pi OS + USB TTL RS485
"""

import sys
import platform
import random
import subprocess
from datetime import datetime
from typing import List

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QProgressBar,
    QGraphicsDropShadowEffect, QScrollArea,
    QDialog, QLineEdit, QDoubleSpinBox, QPushButton, QMessageBox,
    QTabWidget, QFormLayout, QGroupBox
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QObject, QPoint
)
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QPen,
    QPainterPath, QLinearGradient
)

from config import config
from models import SensorData

# ── Raspberry Pi helpers ─────────────────────────────────────

def get_cpu_temperature() -> str:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return f"{int(f.read()) / 1000:.1f}°C"
    except Exception:
        return "N/A"

def get_memory_usage() -> str:
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        total = int(lines[0].split()[1])
        avail = int(lines[2].split()[1])
        return f"{((total - avail) / total * 100):.0f}%"
    except Exception:
        return "N/A"

def get_ip_address() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "N/A"

# ── Theme ────────────────────────────────────────────────────

class Theme:
    BG_DARK  = "#0d1117"
    BG_CARD  = "#161b22"
    BG_PANEL = "#1c2128"
    BORDER   = "#30363d"

    TEXT_PRIMARY   = "#e6edf3"
    TEXT_SECONDARY = "#8b949e"
    TEXT_MUTED     = "#6e7681"

    COLOR_PH    = "#2da44e"
    COLOR_TSS   = "#d29922"
    COLOR_DEBIT = "#388bfd"
    COLOR_COD   = "#f85149"
    COLOR_NH3N  = "#a371f7"

    STATUS_NORMAL  = "#2da44e"
    STATUS_WARNING = "#d29922"
    STATUS_ALARM   = "#f85149"
    STATUS_OFFLINE = "#6e7681"

    FONT = "DejaVu Sans" if platform.system() == "Linux" else "Segoe UI"
    MONO = "DejaVu Sans Mono" if platform.system() == "Linux" else "Consolas"

# ── Signals ──────────────────────────────────────────────────

class SignalBridge(QObject):
    sensor_update    = pyqtSignal(object)
    connection_update = pyqtSignal(bool)
    notification     = pyqtSignal(str, int)
    data_count_update = pyqtSignal(int, int)
    daily_data_update = pyqtSignal(int)

# ── Sparkline ────────────────────────────────────────────────

class SparklineGraph(QWidget):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.points: List[float] = [0.0] * 20
        self.setFixedHeight(40)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def push(self, val: float):
        self.points.pop(0)
        self.points.append(val)
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mn, mx = min(self.points), max(self.points)
        rng = mx - mn if mx != mn else 1.0
        step = w / (len(self.points) - 1)

        pts = [QPoint(int(i * step),
                      int(h - (v - mn) / rng * h * 0.8 - 4))
               for i, v in enumerate(self.points)]

        path = QPainterPath()
        path.moveTo(pts[0].x(), pts[0].y())
        for i in range(len(pts) - 1):
            cx = (pts[i].x() + pts[i+1].x()) // 2
            path.cubicTo(cx, pts[i].y(), cx, pts[i+1].y(),
                         pts[i+1].x(), pts[i+1].y())

        fill = QPainterPath(path)
        fill.lineTo(w, h); fill.lineTo(0, h); fill.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        c = self.color
        grad.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 90))
        grad.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 0))
        painter.fillPath(fill, grad)

        pen = QPen(self.color, 1)
        painter.setPen(pen)
        painter.drawPath(path)

# ── Status Badge ─────────────────────────────────────────────

class StatusBadge(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(66, 18)
        lbl = QLabel("NORMAL", self)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setGeometry(0, 0, 66, 18)
        self._lbl = lbl
        self.set_status("NORMAL")

    def set_status(self, s: str):
        s = s.upper()
        self._lbl.setText(s)
        c = {
            "NORMAL": Theme.STATUS_NORMAL,
            "WARNING": Theme.STATUS_WARNING,
            "ALARM": Theme.STATUS_ALARM,
        }.get(s, Theme.STATUS_OFFLINE)
        self.setStyleSheet(f"QFrame{{background:{c}22;border:1px solid {c};border-radius:9px;}}")
        self._lbl.setStyleSheet(f"color:{c};font-weight:bold;font-size:9px;border:none;background:transparent;")

# ── Sensor Card ──────────────────────────────────────────────

class SensorCard(QFrame):
    """Compact sensor card untuk layar 1024x600"""

    def __init__(self, title, unit, color, lo=0.0, hi=100.0, parent=None):
        super().__init__(parent)
        self.lo, self.hi = lo, hi
        self.setStyleSheet(f"""
            QFrame{{background:{Theme.BG_CARD};border:1px solid {Theme.BORDER};border-radius:6px;}}
            QFrame:hover{{border:1px solid {color};}}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(4)

        # — header row
        hdr = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(f"color:#ccc;font-size:11px;font-weight:bold;font-family:'{Theme.FONT}';border:none;background:transparent;")
        self.badge = StatusBadge()
        hdr.addWidget(t)
        hdr.addStretch()
        hdr.addWidget(self.badge)
        root.addLayout(hdr)

        # — value
        self.val_lbl = QLabel("--")
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.val_lbl.setStyleSheet(f"""
            color:{color};font-size:42px;font-weight:bold;
            font-family:'{Theme.MONO}';border:none;background:transparent;
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(14)
        shadow.setColor(QColor(color))
        shadow.setOffset(0, 0)
        self.val_lbl.setGraphicsEffect(shadow)
        root.addWidget(self.val_lbl)

        # — unit
        u = QLabel(unit)
        u.setAlignment(Qt.AlignmentFlag.AlignCenter)
        u.setStyleSheet("color:#666;font-size:11px;border:none;background:transparent;")
        root.addWidget(u)

        # — baku mutu
        lim = QLabel(f"Baku Mutu: {lo} – {hi} {unit}")
        lim.setStyleSheet("color:#555;font-size:9px;border:none;background:transparent;")
        root.addWidget(lim)

        # — sparkline
        self.graph = SparklineGraph(color)
        root.addWidget(self.graph)

    def update_value(self, v: float):
        self.val_lbl.setText(f"{v:.2f}")
        self.graph.push(v)
        if self.lo <= v <= self.hi:
            self.badge.set_status("NORMAL")
        elif v > self.hi * 1.5:
            self.badge.set_status("ALARM")
        else:
            self.badge.set_status("WARNING")

# ── Sidebar Section ──────────────────────────────────────────

class SidebarSection(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame{{background:{Theme.BG_PANEL};border:1px solid {Theme.BORDER};border-radius:5px;}}
        """)
        self._vbox = QVBoxLayout(self)
        self._vbox.setContentsMargins(8, 7, 8, 7)
        self._vbox.setSpacing(3)

        t = QLabel(title)
        t.setStyleSheet(f"color:{Theme.STATUS_NORMAL};font-weight:bold;font-size:10px;border:none;background:transparent;")
        self._vbox.addWidget(t)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{Theme.BORDER};border:none;max-height:1px;")
        self._vbox.addWidget(sep)

    def add_row(self, label: str, value: str, vc=None) -> QLabel:
        vc = vc or Theme.TEXT_PRIMARY
        row = QHBoxLayout()
        row.setSpacing(4)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{Theme.TEXT_SECONDARY};font-size:10px;border:none;")
        val = QLabel(value)
        val.setStyleSheet(f"color:{vc};font-size:10px;font-weight:bold;border:none;")
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(val)
        self._vbox.addLayout(row)
        return val

    def add_widget(self, w):
        self._vbox.addWidget(w)

# ── Settings Dialog ──────────────────────────────────────────

_DLGSTYLE = f"""
QDialog{{background:{Theme.BG_DARK};}}
QTabWidget::pane{{border:1px solid {Theme.BORDER};background:{Theme.BG_PANEL};border-radius:4px;}}
QTabBar::tab{{background:{Theme.BG_CARD};color:{Theme.TEXT_SECONDARY};border:1px solid {Theme.BORDER};padding:6px 16px;margin-right:2px;border-top-left-radius:4px;border-top-right-radius:4px;}}
QTabBar::tab:selected{{background:{Theme.BG_PANEL};color:{Theme.STATUS_NORMAL};border-bottom:2px solid {Theme.STATUS_NORMAL};}}
QGroupBox{{color:{Theme.TEXT_PRIMARY};border:1px solid {Theme.BORDER};border-radius:5px;margin-top:8px;padding-top:12px;font-weight:bold;font-size:11px;}}
QGroupBox::title{{subcontrol-origin:margin;left:8px;padding:0 4px;}}
QLabel{{color:{Theme.TEXT_SECONDARY};font-size:11px;}}
QLineEdit{{background:{Theme.BG_CARD};color:{Theme.TEXT_PRIMARY};border:1px solid {Theme.BORDER};border-radius:4px;padding:5px 8px;font-size:11px;}}
QLineEdit:focus{{border:1px solid {Theme.STATUS_NORMAL};}}
QDoubleSpinBox{{background:{Theme.BG_CARD};color:{Theme.TEXT_PRIMARY};border:1px solid {Theme.BORDER};border-radius:4px;padding:5px 8px;font-size:12px;font-weight:bold;}}
QDoubleSpinBox:focus{{border:1px solid {Theme.STATUS_NORMAL};}}
QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:18px;border:none;background:#30363d;}}
QDoubleSpinBox::up-button:hover,QDoubleSpinBox::down-button:hover{{background:{Theme.STATUS_NORMAL};}}
QPushButton{{background:{Theme.BG_CARD};color:{Theme.TEXT_PRIMARY};border:1px solid {Theme.BORDER};border-radius:4px;padding:7px 14px;font-size:11px;font-weight:bold;}}
QPushButton:hover{{border:1px solid {Theme.STATUS_NORMAL};color:{Theme.STATUS_NORMAL};}}
QPushButton#btnSave{{background:{Theme.STATUS_NORMAL};color:white;border:none;}}
QPushButton#btnSave:hover{{background:#3ab55a;}}
QPushButton#btnWifi{{background:#1f6feb;color:white;border:none;}}
QPushButton#btnWifi:hover{{background:#388bfd;}}
"""

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pengaturan Sistem")
        self.setFixedSize(480, 420)
        self.setStyleSheet(_DLGSTYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel("PENGATURAN SISTEM")
        title.setStyleSheet(f"color:{Theme.TEXT_PRIMARY};font-size:14px;font-weight:bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._tab_server(), "Server KLHK")
        tabs.addTab(self._tab_wifi(), "WiFi")
        tabs.addTab(self._tab_offset(), "Offset Sensor")
        root.addWidget(tabs)

        btns = QHBoxLayout()
        btns.addStretch()
        bc = QPushButton("Batal"); bc.clicked.connect(self.reject)
        bs = QPushButton("Simpan"); bs.setObjectName("btnSave"); bs.clicked.connect(self._save)
        btns.addWidget(bc); btns.addWidget(bs)
        root.addLayout(btns)

    # ── Tab Server KLHK ──────────────────────────────────────
    def _tab_server(self):
        w = QWidget()
        vb = QVBoxLayout(w); vb.setSpacing(8)
        grp = QGroupBox("Server 2 – KLHK")
        fm = QFormLayout(grp); fm.setSpacing(8); fm.setContentsMargins(10, 16, 10, 10)

        self.e_uid2 = QLineEdit(config.server.uid_2)
        self.e_uid2.setPlaceholderText("UID KLHK")
        fm.addRow("UID KLHK:", self.e_uid2)

        self.e_url2 = QLineEdit(config.server.server_url_2)
        fm.addRow("Server URL:", self.e_url2)

        self.e_sk2 = QLineEdit(config.server.secret_key_url_2)
        fm.addRow("Secret Key URL:", self.e_sk2)

        vb.addWidget(grp)
        note = QLabel("Perubahan aktif setelah restart aplikasi.")
        note.setStyleSheet(f"color:{Theme.STATUS_WARNING};font-size:10px;")
        vb.addWidget(note)
        vb.addStretch()
        return w

    # ── Tab WiFi ─────────────────────────────────────────────
    def _tab_wifi(self):
        w = QWidget()
        vb = QVBoxLayout(w); vb.setSpacing(8)
        grp = QGroupBox("Koneksi WiFi")
        fm = QFormLayout(grp); fm.setSpacing(8); fm.setContentsMargins(10, 16, 10, 10)

        self.e_ssid = QLineEdit(config.network.wifi_ssid)
        self.e_ssid.setPlaceholderText("Nama WiFi")
        fm.addRow("SSID:", self.e_ssid)

        self.e_wpass = QLineEdit(config.network.wifi_password)
        self.e_wpass.setEchoMode(QLineEdit.EchoMode.Password)
        self.e_wpass.setPlaceholderText("Password")
        fm.addRow("Password:", self.e_wpass)

        self.btn_show = QPushButton("Tampilkan")
        self.btn_show.setFixedWidth(90)
        self.btn_show.clicked.connect(self._toggle_pass)
        fm.addRow("", self.btn_show)

        vb.addWidget(grp)

        btn_c = QPushButton("Hubungkan WiFi Sekarang")
        btn_c.setObjectName("btnWifi")
        btn_c.clicked.connect(self._connect_wifi)
        vb.addWidget(btn_c)

        self.lbl_ws = QLabel("Status: –")
        self.lbl_ws.setStyleSheet(f"color:{Theme.TEXT_SECONDARY};font-size:10px;")
        vb.addWidget(self.lbl_ws)

        note = QLabel("Menggunakan nmcli (NetworkManager).")
        note.setStyleSheet(f"color:{Theme.TEXT_MUTED};font-size:9px;")
        vb.addWidget(note)
        vb.addStretch()
        return w

    # ── Tab Offset ───────────────────────────────────────────
    def _tab_offset(self):
        w = QWidget()
        vb = QVBoxLayout(w); vb.setSpacing(8)
        grp = QGroupBox("Kalibrasi Offset Sensor")
        fm = QFormLayout(grp); fm.setSpacing(8); fm.setContentsMargins(10, 16, 10, 10)

        self.sp_ph = QDoubleSpinBox()
        self.sp_ph.setRange(-14, 14); self.sp_ph.setDecimals(2)
        self.sp_ph.setSingleStep(0.01); self.sp_ph.setValue(config.offsets.ph_offset)
        self.sp_ph.setSuffix("  pH")
        fm.addRow("Offset pH:", self.sp_ph)

        self.sp_tss = QDoubleSpinBox()
        self.sp_tss.setRange(-1000, 1000); self.sp_tss.setDecimals(2)
        self.sp_tss.setSingleStep(0.1); self.sp_tss.setValue(config.offsets.tss_offset)
        self.sp_tss.setSuffix("  mg/L")
        fm.addRow("Offset TSS:", self.sp_tss)

        self.sp_dbt = QDoubleSpinBox()
        self.sp_dbt.setRange(-1000, 1000); self.sp_dbt.setDecimals(2)
        self.sp_dbt.setSingleStep(0.1); self.sp_dbt.setValue(config.offsets.debit_offset)
        self.sp_dbt.setSuffix("  m\u00b3/jam")
        fm.addRow("Offset Debit:", self.sp_dbt)

        vb.addWidget(grp)

        br = QPushButton("Reset Semua ke 0")
        br.clicked.connect(lambda: [self.sp_ph.setValue(0),
                                    self.sp_tss.setValue(0),
                                    self.sp_dbt.setValue(0)])
        vb.addWidget(br)

        note = QLabel("pH: nilai+offset (maks 14) | TSS/Debit: nilai−offset")
        note.setStyleSheet(f"color:{Theme.TEXT_MUTED};font-size:9px;")
        note.setWordWrap(True)
        vb.addWidget(note)
        vb.addStretch()
        return w

    # ── Actions ──────────────────────────────────────────────
    def _toggle_pass(self):
        if self.e_wpass.echoMode() == QLineEdit.EchoMode.Password:
            self.e_wpass.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_show.setText("Sembunyikan")
        else:
            self.e_wpass.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_show.setText("Tampilkan")

    def _connect_wifi(self):
        ssid = self.e_ssid.text().strip()
        pw   = self.e_wpass.text().strip()
        if not ssid:
            self._ws("SSID kosong!", Theme.STATUS_ALARM); return
        self._ws("Menghubungkan...", Theme.STATUS_WARNING)
        try:
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
            if pw:
                cmd += ["password", pw]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                self._ws(f"Terhubung ke {ssid}", Theme.STATUS_NORMAL)
            else:
                self._ws((r.stderr or r.stdout).strip()[:90], Theme.STATUS_ALARM)
        except FileNotFoundError:
            self._ws("nmcli tidak ditemukan", Theme.STATUS_ALARM)
        except subprocess.TimeoutExpired:
            self._ws("Timeout", Theme.STATUS_ALARM)
        except Exception as e:
            self._ws(str(e)[:70], Theme.STATUS_ALARM)

    def _ws(self, msg: str, color: str):
        self.lbl_ws.setText(f"Status: {msg}")
        self.lbl_ws.setStyleSheet(f"color:{color};font-size:10px;")

    def _save(self):
        config.server.uid_2            = self.e_uid2.text().strip()
        config.server.server_url_2     = self.e_url2.text().strip()
        config.server.secret_key_url_2 = self.e_sk2.text().strip()
        config.network.wifi_ssid       = self.e_ssid.text().strip()
        config.network.wifi_password   = self.e_wpass.text().strip()
        config.offsets.ph_offset       = self.sp_ph.value()
        config.offsets.tss_offset      = self.sp_tss.value()
        config.offsets.debit_offset    = self.sp_dbt.value()
        config.save()
        QMessageBox.information(self, "Tersimpan", "Pengaturan berhasil disimpan.")
        self.accept()

# ── Main Window ──────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Main window optimized for 7-inch 1024×600 display"""

    def __init__(self):
        super().__init__()
        self.signal_bridge = SignalBridge()
        self.setWindowTitle("SPARING – IPAL Monitoring")
        self.resize(1024, 600)
        self.setStyleSheet(f"background:{Theme.BG_DARK};")

        self.signal_bridge.sensor_update.connect(self._on_sensor)
        self.signal_bridge.connection_update.connect(self._on_connection)
        self.signal_bridge.data_count_update.connect(self._on_count)
        self.signal_bridge.daily_data_update.connect(self._on_daily)

        self._real = False
        self._t0   = datetime.now()

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        cw = QWidget()
        cw.setLayout(root)
        self.setCentralWidget(cw)

        self._build_header(root)

        body = QHBoxLayout()
        body.setContentsMargins(8, 8, 8, 8)
        body.setSpacing(8)
        self._build_cards(body)
        self._build_sidebar(body)

        body_w = QWidget()
        body_w.setLayout(body)
        root.addWidget(body_w, 1)

        self._build_footer(root)

        # Simulation timer
        self._sim = QTimer()
        self._sim.timeout.connect(self._simulate)
        self._sim.start(2000)

    # ── Header ───────────────────────────────────────────────
    def _build_header(self, root):
        hdr = QFrame()
        hdr.setFixedHeight(44)
        hdr.setStyleSheet(f"QFrame{{background:{Theme.BG_PANEL};border-bottom:1px solid {Theme.BORDER};}}")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 12, 0)
        hl.setSpacing(8)

        logo = QLabel("MITRA MUTIARA")
        logo.setStyleSheet(f"color:{Theme.TEXT_PRIMARY};font-size:14px;font-weight:800;border:none;")
        hl.addWidget(logo)

        self._lbl_status = QLabel("ONLINE")
        self._lbl_status.setStyleSheet(
            f"color:{Theme.STATUS_NORMAL};font-weight:bold;font-size:9px;"
            f"border:1px solid {Theme.STATUS_NORMAL};border-radius:3px;padding:1px 5px;"
        )
        hl.addWidget(self._lbl_status)
        hl.addStretch()

        # Clock
        self._lbl_time = QLabel("--:--:--")
        self._lbl_time.setStyleSheet(
            f"color:{Theme.TEXT_PRIMARY};font-size:18px;font-weight:bold;"
            f"font-family:'{Theme.MONO}';border:none;"
        )
        hl.addWidget(self._lbl_time)

        self._lbl_date = QLabel(datetime.now().strftime("%d %b %Y"))
        self._lbl_date.setStyleSheet(f"color:{Theme.TEXT_MUTED};font-size:9px;border:none;")
        hl.addWidget(self._lbl_date)

        hl.addStretch()

        # Buttons row
        def hbtn(text, color="#555", bc="#333"):
            b = QLabel(text)
            b.setStyleSheet(
                f"color:{color};border:1px solid {bc};border-radius:3px;"
                f"padding:3px 8px;font-size:9px;font-weight:bold;"
            )
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            return b

        fs = hbtn("FULLSCREEN")
        fs.mousePressEvent = self._toggle_fs
        hl.addWidget(fs)

        st = hbtn("SETTINGS", Theme.STATUS_WARNING, Theme.STATUS_WARNING)
        st.mousePressEvent = self._open_settings
        hl.addWidget(st)

        hl.addSpacing(6)

        self._lbl_ip   = hbtn(f"IP: {get_ip_address()}")
        self._lbl_cpu  = hbtn("CPU: --")
        self._lbl_ram  = hbtn("RAM: --")
        hl.addWidget(self._lbl_ip)
        hl.addWidget(self._lbl_cpu)
        hl.addWidget(self._lbl_ram)

        root.addWidget(hdr)

        self._clock = QTimer()
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)
        self._tick_n = 0

    def _tick(self):
        self._lbl_time.setText(datetime.now().strftime("%H:%M:%S"))
        self._tick_n += 1
        if self._tick_n % 5 == 0:
            temp = get_cpu_temperature()
            self._lbl_cpu.setText(f"CPU: {temp}")
            try:
                tv = float(temp.replace("°C", ""))
                col = Theme.STATUS_ALARM if tv > 70 else (Theme.STATUS_WARNING if tv > 55 else "#555")
                bc  = col
            except ValueError:
                col, bc = "#555", "#333"
            self._lbl_cpu.setStyleSheet(
                f"color:{col};border:1px solid {bc};border-radius:3px;"
                f"padding:3px 8px;font-size:9px;font-weight:bold;"
            )
            self._lbl_ram.setText(f"RAM: {get_memory_usage()}")

    # ── Sensor Cards ─────────────────────────────────────────
    def _build_cards(self, parent):
        cw = QWidget()
        hl = QHBoxLayout(cw)
        hl.setSpacing(6)
        hl.setContentsMargins(0, 0, 0, 0)

        self.c_ph    = SensorCard("pH",    "",       Theme.COLOR_PH,    6.0, 9.0)
        self.c_tss   = SensorCard("TSS",   "mg/L",   Theme.COLOR_TSS,   0, 200)
        self.c_debit = SensorCard("DEBIT", "m\u00b3/j", Theme.COLOR_DEBIT, 0, 100)
        self.c_cod   = SensorCard("COD",   "mg/L",   Theme.COLOR_COD,   0, 300)
        self.c_nh3n  = SensorCard("NH3-N", "mg/L",   Theme.COLOR_NH3N,  0, 10)

        for c in (self.c_ph, self.c_tss, self.c_debit, self.c_cod, self.c_nh3n):
            hl.addWidget(c)

        parent.addWidget(cw, 3)   # 75% width

    # ── Sidebar ──────────────────────────────────────────────
    def _build_sidebar(self, parent):
        sc = QWidget()
        vb = QVBoxLayout(sc)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(6)

        # Modbus RS485
        s_mb = SidebarSection("MODBUS RS485 (USB)")
        self._lbl_port      = s_mb.add_row("Port", config.modbus.port)
        self._lbl_last_read = s_mb.add_row("Last Read", "--:--:--")
        self._lbl_akuisisi  = s_mb.add_row("Status", "Waiting...", Theme.STATUS_WARNING)
        self._s_mb = s_mb

        # Modbus indicators
        ind_row = QHBoxLayout()
        ind_row.setSpacing(3)
        self._mb_inds = {}
        for num, name in [("01","PH"),("02","TSS"),("03","FLW"),("04","COD"),("05","NH3")]:
            lbl = QLabel(f"{num}\n{name}")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"background:{Theme.STATUS_OFFLINE}22;color:{Theme.STATUS_OFFLINE};"
                f"border:1px solid {Theme.STATUS_OFFLINE};border-radius:3px;font-size:9px;padding:2px;"
            )
            ind_row.addWidget(lbl)
            self._mb_inds[name] = lbl
        s_mb._vbox.addLayout(ind_row)
        vb.addWidget(s_mb)

        # Kelistrikan
        s_el = SidebarSection("KELISTRIKAN")
        self._lbl_volt  = s_el.add_row("Tegangan", "0.00 V")
        self._lbl_curr  = s_el.add_row("Arus",     "0.00 A")
        self._lbl_power = s_el.add_row("Daya",     "0.00 W")
        self._s_el = s_el
        vb.addWidget(s_el)

        # Server Mitra Mutiara
        s_mm = SidebarSection("SERVER MITRA MUTIARA")
        self._lbl_mm = s_mm.add_row("Koneksi", "Checking...", Theme.TEXT_SECONDARY)
        self._s_mm = s_mm
        vb.addWidget(s_mm)

        # Server KLHK
        s_kl = SidebarSection("SERVER KLHK")
        self._lbl_kl = s_kl.add_row("Koneksi", "Checking...", Theme.TEXT_SECONDARY)
        s_kl.add_row("UID", config.server.uid_2)
        self._s_kl = s_kl
        vb.addWidget(s_kl)

        # Buffer
        s_buf = SidebarSection("DATA BUFFER")
        self._lbl_buf = s_buf.add_row("Buffered", "0 records")
        self._buf_bar = QProgressBar()
        self._buf_bar.setFixedHeight(4)
        self._buf_bar.setValue(0)
        self._buf_bar.setStyleSheet(
            f"QProgressBar{{background:#333;border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{Theme.COLOR_TSS};border-radius:2px;}}"
        )
        s_buf.add_widget(self._buf_bar)
        self._s_buf = s_buf
        vb.addWidget(s_buf)

        # Alarm box
        self._alarm_box = QFrame()
        self._alarm_box.setStyleSheet(
            f"background:{Theme.STATUS_ALARM}18;border:1px solid {Theme.STATUS_ALARM};border-radius:5px;"
        )
        ab = QVBoxLayout(self._alarm_box)
        ab.setContentsMargins(8, 6, 8, 6)
        self._alarm_title = QLabel("Tidak ada alarm")
        self._alarm_title.setStyleSheet(f"color:{Theme.STATUS_NORMAL};font-weight:bold;font-size:10px;border:none;")
        self._alarm_desc  = QLabel("Semua parameter normal.")
        self._alarm_desc.setWordWrap(True)
        self._alarm_desc.setStyleSheet(f"color:{Theme.TEXT_SECONDARY};font-size:9px;border:none;")
        ab.addWidget(self._alarm_title)
        ab.addWidget(self._alarm_desc)
        vb.addWidget(self._alarm_box)

        vb.addStretch()

        # Wrap in scroll area
        sa = QScrollArea()
        sa.setWidget(sc)
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setStyleSheet("""
            QScrollArea{border:none;background:transparent;}
            QScrollArea>QWidget>QWidget{background:transparent;}
            QScrollBar:vertical{border:none;background:#0d1117;width:5px;}
            QScrollBar::handle:vertical{background:#333;min-height:20px;border-radius:2px;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}
        """)
        parent.addWidget(sa, 1)   # 25% width

    # ── Footer ───────────────────────────────────────────────
    def _build_footer(self, root):
        ft = QFrame()
        ft.setFixedHeight(46)
        ft.setStyleSheet(f"QFrame{{background:{Theme.BG_PANEL};border-top:1px solid {Theme.BORDER};}}")
        hl = QHBoxLayout(ft)
        hl.setContentsMargins(12, 4, 12, 4)
        hl.setSpacing(0)

        def stat(label, init_val, sub, color):
            f = QFrame()
            f.setStyleSheet("background:transparent;border:none;")
            fl = QHBoxLayout(f)
            fl.setSpacing(6)
            vl = QVBoxLayout()
            vl.setSpacing(1)
            l1 = QLabel(label)
            l1.setStyleSheet("color:#666;font-size:9px;")
            lv = QLabel(init_val)
            lv.setStyleSheet(f"color:{Theme.TEXT_PRIMARY};font-weight:bold;font-size:13px;")
            l3 = QLabel(sub)
            l3.setStyleSheet(f"color:{color};font-size:9px;")
            vl.addWidget(l1); vl.addWidget(lv); vl.addWidget(l3)
            fl.addLayout(vl)
            return f, lv

        u_blk, self._lbl_uptime = stat("Uptime", "0h 0m", "Raspberry Pi OS", Theme.STATUS_NORMAL)
        hl.addWidget(u_blk)
        hl.addStretch()

        s_blk, self._lbl_sent = stat("Data Terkirim Hari Ini", "0", "USB RS485", Theme.STATUS_NORMAL)
        hl.addWidget(s_blk)
        hl.addStretch()

        a_blk, self._lbl_alarms = stat("Alarm Aktif", "0", "Normal", Theme.STATUS_ALARM)
        hl.addWidget(a_blk)
        hl.addStretch()

        k_blk, _ = stat("Kepatuhan Bulan Ini", "94.2%", "Target >90%", Theme.STATUS_NORMAL)
        hl.addWidget(k_blk)

        root.addWidget(ft)

        self._uptime_timer = QTimer()
        self._uptime_timer.timeout.connect(self._uptime)
        self._uptime_timer.start(60000)

    def _uptime(self):
        d = datetime.now() - self._t0
        h = d.seconds // 3600
        m = (d.seconds % 3600) // 60
        self._lbl_uptime.setText(f"{d.days}d {h}h {m}m")

    # ── Slots ────────────────────────────────────────────────
    def _toggle_fs(self, _):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _open_settings(self, _):
        SettingsDialog(self).exec()

    def _simulate(self):
        if self._real: return
        self.c_ph.update_value(random.uniform(7.0, 7.5))
        self.c_tss.update_value(random.uniform(180, 200))
        self.c_debit.update_value(random.uniform(22, 28))
        self.c_cod.update_value(random.uniform(290, 320))
        self.c_nh3n.update_value(random.uniform(4.0, 5.5))

    def _on_sensor(self, data: SensorData):
        self._real = True
        self.c_ph.update_value(data.ph)
        self.c_tss.update_value(data.tss)
        self.c_debit.update_value(data.debit)
        self.c_cod.update_value(data.cod)
        self.c_nh3n.update_value(data.nh3n)

        # Modbus indicators → green
        for lbl in self._mb_inds.values():
            lbl.setStyleSheet(
                f"background:{Theme.STATUS_NORMAL}22;color:{Theme.STATUS_NORMAL};"
                f"border:1px solid {Theme.STATUS_NORMAL};border-radius:3px;font-size:9px;padding:2px;"
            )
        self._lbl_last_read.setText(datetime.fromtimestamp(data.timestamp).strftime("%H:%M:%S"))
        self._lbl_akuisisi.setText("Running")
        self._lbl_akuisisi.setStyleSheet(f"color:{Theme.STATUS_NORMAL};font-size:10px;font-weight:bold;border:none;")

        self._lbl_volt.setText(f"{data.voltage:.2f} V")
        self._lbl_curr.setText(f"{data.current:.2f} A")
        self._lbl_power.setText(f"{data.voltage * data.current:.2f} W")

        self._check_alarms(data)

    def _on_connection(self, ok: bool):
        txt   = "Connected" if ok else "Disconnected"
        color = Theme.STATUS_NORMAL if ok else Theme.STATUS_OFFLINE
        for lbl in (self._lbl_mm, self._lbl_kl):
            lbl.setText(txt)
            lbl.setStyleSheet(f"color:{color};font-size:10px;font-weight:bold;border:none;")

    def _on_count(self, cur, mx):
        self._lbl_buf.setText(f"{cur} records")
        self._buf_bar.setValue(int(cur / mx * 100) if mx else 0)

    def _on_daily(self, n):
        self._lbl_sent.setText(str(n))

    def _check_alarms(self, d: SensorData):
        msgs = []
        if not (6 <= d.ph <= 9):   msgs.append(f"pH {d.ph:.2f} di luar batas")
        if d.tss > 200:            msgs.append(f"TSS {d.tss:.1f} mg/L melebihi batas")
        if d.cod > 300:            msgs.append(f"COD {d.cod:.1f} mg/L melebihi batas")
        if d.nh3n > 10:            msgs.append(f"NH3-N {d.nh3n:.1f} mg/L melebihi batas")

        n = len(msgs)
        self._lbl_alarms.setText(str(n))
        if n:
            self._lbl_alarms.setStyleSheet(f"color:{Theme.STATUS_ALARM};font-weight:bold;font-size:13px;")
            self._alarm_title.setText(f"ALARM: {n} parameter")
            self._alarm_title.setStyleSheet(f"color:{Theme.STATUS_ALARM};font-weight:bold;font-size:10px;border:none;")
            self._alarm_desc.setText("\n".join(msgs))
        else:
            self._lbl_alarms.setStyleSheet(f"color:{Theme.TEXT_PRIMARY};font-weight:bold;font-size:13px;")
            self._alarm_title.setText("Tidak ada alarm")
            self._alarm_title.setStyleSheet(f"color:{Theme.STATUS_NORMAL};font-weight:bold;font-size:10px;border:none;")
            self._alarm_desc.setText("Semua parameter normal.")

# ── Factory ──────────────────────────────────────────────────

def create_application():
    app = QApplication(sys.argv)
    app.setFont(QFont(Theme.FONT))
    window = MainWindow()
    return app, window

if __name__ == "__main__":
    app, window = create_application()
    window.showFullScreen()
    sys.exit(app.exec())
