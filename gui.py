"""
SPARING Display GUI - v2
Redesigned for 7" 1024x600 display
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
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QPainterPath, QLinearGradient

from config import config
from models import SensorData

# ── RPi helpers ───────────────────────────────────────────────

def _rfile(path, default="N/A"):
    try:
        with open(path) as f: return f.read().strip()
    except Exception: return None

def get_cpu_temp() -> str:
    v = _rfile("/sys/class/thermal/thermal_zone0/temp")
    return f"{int(v)/1000:.1f}°C" if v else "N/A"

def get_mem() -> str:
    try:
        with open("/proc/meminfo") as f: lines = f.readlines()
        total = int(lines[0].split()[1])
        avail = int(lines[2].split()[1])
        return f"{(total-avail)/total*100:.0f}%"
    except Exception: return "N/A"

def get_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception: return "N/A"

# ── Theme ─────────────────────────────────────────────────────

class T:  # Theme
    BG      = "#0d1117"
    CARD    = "#161b22"
    PANEL   = "#1c2128"
    BORDER  = "#30363d"
    BORDER2 = "#21262d"

    FG1 = "#e6edf3"
    FG2 = "#8b949e"
    FG3 = "#6e7681"

    GREEN  = "#2da44e"
    YELLOW = "#d29922"
    BLUE   = "#388bfd"
    RED    = "#f85149"
    PURPLE = "#a371f7"
    CYAN   = "#39c5cf"

    OK   = "#2da44e"
    WARN = "#d29922"
    ERR  = "#f85149"
    OFF  = "#6e7681"

    FONT = "DejaVu Sans" if platform.system() == "Linux" else "Segoe UI"
    MONO = "DejaVu Sans Mono" if platform.system() == "Linux" else "Consolas"

    # Sensor colors & limits
    SENSORS = {
        "pH":    (GREEN,  "",        6.0,   9.0),
        "TSS":   (YELLOW, "mg/L",    0,     200),
        "DEBIT": (BLUE,   "m³/j",   0,     100),
        "COD":   (RED,    "mg/L",    0,     300),
        "NH3-N": (PURPLE, "mg/L",    0,     10),
    }

# ── Signals ───────────────────────────────────────────────────

class SignalBridge(QObject):
    sensor_update     = pyqtSignal(object)
    connection_update = pyqtSignal(bool)
    notification      = pyqtSignal(str, int)
    data_count_update = pyqtSignal(int, int)
    daily_data_update = pyqtSignal(int)

# ── Sparkline ─────────────────────────────────────────────────

class Spark(QWidget):
    def __init__(self, color: str, h=44, parent=None):
        super().__init__(parent)
        self.clr = QColor(color)
        self.pts: List[float] = [0.0] * 30
        self.setFixedHeight(h)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def push(self, v: float):
        self.pts.pop(0); self.pts.append(v); self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mn, mx = min(self.pts), max(self.pts)
        rng = mx - mn if mx != mn else 1.0
        step = w / (len(self.pts) - 1)

        pts = [QPoint(int(i * step),
                      int(h - (v - mn) / rng * (h - 6) - 3))
               for i, v in enumerate(self.pts)]

        path = QPainterPath()
        path.moveTo(pts[0].x(), pts[0].y())
        for i in range(len(pts) - 1):
            cx = (pts[i].x() + pts[i+1].x()) // 2
            path.cubicTo(cx, pts[i].y(), cx, pts[i+1].y(),
                         pts[i+1].x(), pts[i+1].y())

        fill = QPainterPath(path)
        fill.lineTo(w, h); fill.lineTo(0, h); fill.closeSubpath()
        g = QLinearGradient(0, 0, 0, h)
        c = self.clr
        g.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 110))
        g.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
        p.fillPath(fill, g)
        p.setPen(QPen(self.clr, 1.5))
        p.drawPath(path)

# ── Status Badge ──────────────────────────────────────────────

class Badge(QLabel):
    _COLORS = {"NORMAL": T.OK, "WARNING": T.WARN, "ALARM": T.ERR}

    def __init__(self, parent=None):
        super().__init__("NORMAL", parent)
        self.setFixedSize(62, 18)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set("NORMAL")

    def set(self, s: str):
        s = s.upper()
        c = self._COLORS.get(s, T.OFF)
        self.setText(s)
        self.setStyleSheet(
            f"color:{c};font-weight:bold;font-size:9px;"
            f"border:1px solid {c};border-radius:9px;"
            f"background:{c}18;"
        )

# ── Sensor Card ───────────────────────────────────────────────

class SensorCard(QFrame):
    """
    Layout (top → bottom):
      ┌─────────────────────────┐  ← colored top border 3px
      │ [TITLE]        [BADGE]  │  22px
      │ ─────────────────────── │  1px sep
      │                         │  ← stretch
      │      000.00             │  big value  ~52px
      │       unit              │  16px
      │                         │  ← stretch
      │ Baku Mutu: x – y unit   │  14px
      │ ░░░░░░ sparkline ░░░░░  │  44px
      └─────────────────────────┘
    """
    def __init__(self, name, color, unit, lo, hi, parent=None):
        super().__init__(parent)
        self.lo, self.hi = lo, hi
        self._color = color

        # Outer style: colored top border, rounded corners
        self.setStyleSheet(f"""
            SensorCard {{
                background: {T.CARD};
                border: 1px solid {T.BORDER};
                border-top: 3px solid {color};
                border-radius: 6px;
            }}
        """)

        vb = QVBoxLayout(self)
        vb.setContentsMargins(12, 10, 12, 8)
        vb.setSpacing(0)

        # ── row: title + badge ──
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        tl = QLabel(name)
        tl.setStyleSheet(
            f"color:{T.FG2};font-size:10px;font-weight:bold;"
            f"font-family:'{T.FONT}';border:none;background:transparent;"
        )
        self.badge = Badge()
        hdr.addWidget(tl)
        hdr.addStretch()
        hdr.addWidget(self.badge)
        vb.addLayout(hdr)

        # thin separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{T.BORDER2};border:none;margin-top:6px;margin-bottom:0px;")
        vb.addWidget(sep)

        # ── center: value + unit (vertically centered) ──
        vb.addStretch(2)

        self.val = QLabel("–")
        self.val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.val.setStyleSheet(
            f"color:{color};font-size:46px;font-weight:bold;"
            f"font-family:'{T.MONO}';border:none;background:transparent;"
        )
        fx = QGraphicsDropShadowEffect()
        fx.setBlurRadius(18); fx.setColor(QColor(color)); fx.setOffset(0, 0)
        self.val.setGraphicsEffect(fx)
        vb.addWidget(self.val)

        self.unit_lbl = QLabel(unit)
        self.unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unit_lbl.setStyleSheet(
            f"color:{T.FG3};font-size:11px;border:none;background:transparent;"
        )
        vb.addWidget(self.unit_lbl)

        vb.addStretch(2)

        # ── bottom info bar ──
        info_row = QHBoxLayout()
        info_row.setSpacing(0)
        lim = QLabel(f"Baku Mutu  {lo} – {hi} {unit}")
        lim.setStyleSheet(f"color:{T.FG3};font-size:9px;border:none;background:transparent;")
        info_row.addWidget(lim)
        info_row.addStretch()
        vb.addLayout(info_row)

        vb.addSpacing(4)

        self.spark = Spark(color)
        vb.addWidget(self.spark)

    def update_value(self, v: float):
        self.val.setText(f"{v:.2f}")
        self.spark.push(v)
        if self.lo <= v <= self.hi:
            self.badge.set("NORMAL")
        elif v > self.hi * 1.5:
            self.badge.set("ALARM")
        else:
            self.badge.set("WARNING")

# ── Sidebar section ───────────────────────────────────────────

class Section(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background:{T.PANEL};
                border:1px solid {T.BORDER};
                border-radius:5px;
            }}
        """)
        self._vb = QVBoxLayout(self)
        self._vb.setContentsMargins(9, 7, 9, 7)
        self._vb.setSpacing(3)

        th = QLabel(title.upper())
        th.setStyleSheet(
            f"color:{T.OK};font-weight:bold;font-size:9px;"
            f"letter-spacing:1px;border:none;background:transparent;"
        )
        self._vb.addWidget(th)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{T.BORDER};border:none;margin-bottom:2px;")
        self._vb.addWidget(sep)

    def row(self, label, val="–", vc=None) -> QLabel:
        vc = vc or T.FG1
        hl = QHBoxLayout(); hl.setSpacing(4)
        lb = QLabel(label)
        lb.setStyleSheet(f"color:{T.FG2};font-size:10px;border:none;")
        vl = QLabel(val)
        vl.setStyleSheet(f"color:{vc};font-size:10px;font-weight:bold;border:none;")
        vl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(lb); hl.addStretch(); hl.addWidget(vl)
        self._vb.addLayout(hl)
        return vl

    def add(self, w): self._vb.addWidget(w)

# ── Settings Dialog ───────────────────────────────────────────

_DS = f"""
QDialog{{background:{T.BG};}}
QTabWidget::pane{{border:1px solid {T.BORDER};background:{T.PANEL};border-radius:4px;}}
QTabBar::tab{{background:{T.CARD};color:{T.FG2};border:1px solid {T.BORDER};padding:5px 14px;margin-right:2px;border-top-left-radius:4px;border-top-right-radius:4px;}}
QTabBar::tab:selected{{background:{T.PANEL};color:{T.OK};border-bottom:2px solid {T.OK};}}
QGroupBox{{color:{T.FG1};border:1px solid {T.BORDER};border-radius:5px;margin-top:8px;padding-top:12px;font-weight:bold;font-size:11px;}}
QGroupBox::title{{subcontrol-origin:margin;left:8px;padding:0 4px;}}
QLabel{{color:{T.FG2};font-size:11px;}}
QLineEdit{{background:{T.CARD};color:{T.FG1};border:1px solid {T.BORDER};border-radius:4px;padding:5px 8px;font-size:11px;}}
QLineEdit:focus{{border:1px solid {T.OK};}}
QDoubleSpinBox{{background:{T.CARD};color:{T.FG1};border:1px solid {T.BORDER};border-radius:4px;padding:5px 8px;font-size:12px;font-weight:bold;}}
QDoubleSpinBox:focus{{border:1px solid {T.OK};}}
QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:18px;border:none;background:{T.BORDER};}}
QDoubleSpinBox::up-button:hover,QDoubleSpinBox::down-button:hover{{background:{T.OK};}}
QPushButton{{background:{T.CARD};color:{T.FG1};border:1px solid {T.BORDER};border-radius:4px;padding:6px 14px;font-size:11px;font-weight:bold;}}
QPushButton:hover{{border:1px solid {T.OK};color:{T.OK};}}
QPushButton#S{{background:{T.OK};color:white;border:none;}}
QPushButton#S:hover{{background:#3ab55a;}}
QPushButton#W{{background:#1f6feb;color:white;border:none;}}
QPushButton#W:hover{{background:{T.BLUE};}}
"""

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pengaturan Sistem")
        self.setFixedSize(480, 400)
        self.setStyleSheet(_DS)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10); root.setSpacing(8)

        hd = QLabel("PENGATURAN SISTEM")
        hd.setStyleSheet(f"color:{T.FG1};font-size:13px;font-weight:bold;")
        hd.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hd)

        tabs = QTabWidget()
        tabs.addTab(self._t_server(), "Server KLHK")
        tabs.addTab(self._t_wifi(),   "WiFi")
        tabs.addTab(self._t_offset(), "Offset Sensor")
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
        self.e_uid  = QLineEdit(config.server.uid_2)
        self.e_url  = QLineEdit(config.server.server_url_2)
        self.e_sk   = QLineEdit(config.server.secret_key_url_2)
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
        self._btn_show.clicked.connect(self._toggle)
        fm.addRow("SSID:", self.e_ssid)
        fm.addRow("Password:", self.e_pass)
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
        g = QGroupBox("Kalibrasi Offset Sensor")
        fm = QFormLayout(g); fm.setSpacing(8); fm.setContentsMargins(10,16,10,10)

        def spin(lo, hi, step, val, suffix):
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setDecimals(2)
            s.setSingleStep(step); s.setValue(val); s.setSuffix(suffix)
            return s

        self.s_ph  = spin(-14, 14, 0.01, config.offsets.ph_offset,  "  pH")
        self.s_tss = spin(-999,999, 0.1, config.offsets.tss_offset, "  mg/L")
        self.s_dbt = spin(-999,999, 0.1, config.offsets.debit_offset,"  m³/j")
        fm.addRow("Offset pH:", self.s_ph)
        fm.addRow("Offset TSS:", self.s_tss)
        fm.addRow("Offset Debit:", self.s_dbt)
        vb.addWidget(g)
        br = QPushButton("Reset Semua ke 0")
        br.clicked.connect(lambda: [s.setValue(0) for s in [self.s_ph,self.s_tss,self.s_dbt]])
        vb.addWidget(br)
        note = QLabel("pH: nilai+offset (maks 14) | TSS/Debit: nilai−offset")
        note.setStyleSheet(f"color:{T.FG3};font-size:9px;"); note.setWordWrap(True)
        vb.addWidget(note)
        note2 = QLabel("⚠ Offset aktif di mode sensor nyata. Mode dummy tidak menggunakan offset.")
        note2.setStyleSheet(f"color:{T.WARN};font-size:9px;"); note2.setWordWrap(True)
        vb.addWidget(note2)
        vb.addStretch(); return w

    def _toggle(self):
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
            cmd = ["nmcli","device","wifi","connect",ssid]
            if pw: cmd += ["password", pw]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                self._setwstatus(f"Terhubung ke {ssid}", T.OK)
            else:
                self._setwstatus((r.stderr or r.stdout).strip()[:80], T.ERR)
        except FileNotFoundError:
            self._setwstatus("nmcli tidak ditemukan", T.ERR)
        except subprocess.TimeoutExpired:
            self._setwstatus("Timeout", T.ERR)
        except Exception as e:
            self._setwstatus(str(e)[:70], T.ERR)

    def _setwstatus(self, msg, color):
        self._ws.setText(f"Status: {msg}")
        self._ws.setStyleSheet(f"color:{color};font-size:10px;")

    def _save(self):
        config.server.uid_2            = self.e_uid.text().strip()
        config.server.server_url_2     = self.e_url.text().strip()
        config.server.secret_key_url_2 = self.e_sk.text().strip()
        config.network.wifi_ssid       = self.e_ssid.text().strip()
        config.network.wifi_password   = self.e_pass.text().strip()
        config.offsets.ph_offset       = self.s_ph.value()
        config.offsets.tss_offset      = self.s_tss.value()
        config.offsets.debit_offset    = self.s_dbt.value()
        config.save()
        # Refresh sidebar labels di MainWindow
        mw = self.parent()
        if hasattr(mw, 'refresh_sidebar'):
            mw.refresh_sidebar()
        QMessageBox.information(self, "Tersimpan", "Pengaturan berhasil disimpan.")
        self.accept()

# ── Main Window ───────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.signal_bridge = SignalBridge()
        self.setWindowTitle("SPARING – IPAL Monitoring")
        self.resize(1024, 600)
        self.setStyleSheet(f"QMainWindow{{background:{T.BG};}}")

        self.signal_bridge.sensor_update.connect(self._on_sensor)
        self.signal_bridge.connection_update.connect(self._on_conn)
        self.signal_bridge.data_count_update.connect(self._on_count)
        self.signal_bridge.daily_data_update.connect(self._on_daily)

        self._real = False
        self._t0   = datetime.now()
        self._tick_n = 0

        cw = QWidget()
        cw.setStyleSheet(f"background:{T.BG};")
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._mk_header(root)

        body = QWidget()
        body.setStyleSheet(f"background:{T.BG};")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(8, 8, 8, 6)
        bl.setSpacing(8)
        self._mk_cards(bl)
        self._mk_sidebar(bl)
        root.addWidget(body, 1)

        self._mk_footer(root)

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
        hl.setContentsMargins(14, 0, 14, 0)
        hl.setSpacing(8)

        # Logo
        logo = QLabel("⬡ MITRA MUTIARA")
        logo.setStyleSheet(
            f"color:{T.FG1};font-size:13px;font-weight:800;border:none;"
        )
        hl.addWidget(logo)

        self._h_status = QLabel("● ONLINE")
        self._h_status.setStyleSheet(
            f"color:{T.OK};font-size:9px;font-weight:bold;border:none;"
        )
        hl.addWidget(self._h_status)

        hl.addStretch()

        # Clock
        self._h_time = QLabel("--:--:--")
        self._h_time.setStyleSheet(
            f"color:{T.FG1};font-size:20px;font-weight:bold;"
            f"font-family:'{T.MONO}';border:none;"
        )
        hl.addWidget(self._h_time)

        self._h_date = QLabel(datetime.now().strftime("%d %b %Y"))
        self._h_date.setStyleSheet(f"color:{T.FG3};font-size:9px;border:none;")
        hl.addWidget(self._h_date)

        hl.addStretch()

        # Action + system info chips
        def chip(text, color=T.FG3, bc=T.BORDER):
            lb = QLabel(text)
            lb.setStyleSheet(
                f"color:{color};border:1px solid {bc};border-radius:3px;"
                f"padding:2px 8px;font-size:9px;font-weight:bold;"
            )
            lb.setCursor(Qt.CursorShape.PointingHandCursor)
            return lb

        def _on_fullscreen(_):
            if self.isFullScreen(): self.showNormal()
            else: self.showFullScreen()

        def _on_settings(_):
            SettingsDialog(self).exec()

        fs = chip("⛶ FULLSCREEN")
        fs.mousePressEvent = _on_fullscreen
        hl.addWidget(fs)

        st = chip("⚙ SETTINGS", T.WARN, T.WARN)
        st.mousePressEvent = _on_settings
        hl.addWidget(st)

        hl.addSpacing(4)

        self._chip_ip  = chip(f"🌐 {get_ip()}")
        self._chip_cpu = chip("🌡 CPU: –")
        self._chip_ram = chip("💾 RAM: –")
        for c in (self._chip_ip, self._chip_cpu, self._chip_ram):
            hl.addWidget(c)

        root.addWidget(hdr)

        self._clk = QTimer()
        self._clk.timeout.connect(self._tick)
        self._clk.start(1000)

    def _tick(self):
        self._h_time.setText(datetime.now().strftime("%H:%M:%S"))
        self._tick_n += 1
        if self._tick_n % 5 == 0:
            temp = get_cpu_temp()
            try:
                tv = float(temp.replace("°C",""))
                col = T.ERR if tv > 70 else (T.WARN if tv > 55 else T.FG3)
                bc  = col
            except ValueError:
                col, bc = T.FG3, T.BORDER
            self._chip_cpu.setText(f"🌡 {temp}")
            self._chip_cpu.setStyleSheet(
                f"color:{col};border:1px solid {bc};border-radius:3px;"
                f"padding:2px 8px;font-size:9px;font-weight:bold;"
            )
            self._chip_ram.setText(f"💾 {get_mem()}")

    # ─────────────────────────────────────── Cards ──

    def _mk_cards(self, parent):
        cw = QWidget()
        cw.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(cw)
        hl.setSpacing(6)
        hl.setContentsMargins(0, 0, 0, 0)

        self.cards = {}
        for name, (color, unit, lo, hi) in T.SENSORS.items():
            card = SensorCard(name, color, unit, lo, hi)
            hl.addWidget(card)
            self.cards[name] = card

        parent.addWidget(cw, 3)   # 75% width

    # ─────────────────────────────────────── Sidebar ──

    def _mk_sidebar(self, parent):
        sc = QWidget()
        sc.setStyleSheet("background:transparent;")
        vb = QVBoxLayout(sc)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(5)

        # Modbus RS485
        s_mb = Section("Modbus RS485 (USB)")
        self._mb_port = s_mb.row("Port", config.modbus.port)
        self._mb_last = s_mb.row("Last Read", "–:–:–")
        self._mb_stat = s_mb.row("Status", "Waiting…", T.WARN)

        # LED indicators
        led_row = QHBoxLayout(); led_row.setSpacing(3)
        self._leds = {}
        for num, name in [("01","PH"),("02","TSS"),("03","FLW"),("04","COD"),("05","NH3")]:
            lb = QLabel(f"{num}\n{name}")
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setFixedWidth(36)
            lb.setStyleSheet(
                f"background:{T.OFF}20;color:{T.OFF};"
                f"border:1px solid {T.OFF};border-radius:3px;"
                f"font-size:9px;padding:2px;"
            )
            led_row.addWidget(lb)
            self._leds[name] = lb
        s_mb._vb.addLayout(led_row)
        vb.addWidget(s_mb)

        # Kelistrikan
        s_el = Section("Kelistrikan")
        self._v_volt  = s_el.row("Tegangan", "0.00 V")
        self._v_curr  = s_el.row("Arus",     "0.00 A")
        self._v_power = s_el.row("Daya",     "0.00 W")
        vb.addWidget(s_el)

        # Server 1
        s_mm = Section("Server Mitra Mutiara")
        self._mm_conn = s_mm.row("Koneksi", "Checking…", T.FG2)
        vb.addWidget(s_mm)

        # Server 2
        s_kl = Section("Server KLHK")
        self._kl_conn = s_kl.row("Koneksi", "Checking…", T.FG2)
        self._kl_uid  = s_kl.row("UID", config.server.uid_2)
        vb.addWidget(s_kl)

        # Offset Kalibrasi
        s_off = Section("Offset Kalibrasi")
        self._off_ph  = s_off.row("pH",    f"{config.offsets.ph_offset:+.2f}")
        self._off_tss = s_off.row("TSS",   f"{config.offsets.tss_offset:+.2f} mg/L")
        self._off_dbt = s_off.row("Debit", f"{config.offsets.debit_offset:+.2f} m³/j")
        vb.addWidget(s_off)

        # Buffer
        s_buf = Section("Data Buffer")
        self._buf_lbl = s_buf.row("Tersimpan", "0 records")
        bar = QProgressBar()
        bar.setFixedHeight(5); bar.setValue(0)
        bar.setStyleSheet(
            f"QProgressBar{{background:#333;border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{T.YELLOW};border-radius:2px;}}"
        )
        self._buf_bar = bar
        s_buf.add(bar)
        vb.addWidget(s_buf)

        # Alarm
        self._alarm_frame = QFrame()
        self._alarm_frame.setStyleSheet(
            f"background:{T.ERR}18;border:1px solid {T.ERR};border-radius:5px;"
        )
        af = QVBoxLayout(self._alarm_frame)
        af.setContentsMargins(8, 6, 8, 6); af.setSpacing(3)
        self._alarm_title = QLabel("● Tidak ada alarm")
        self._alarm_title.setStyleSheet(f"color:{T.OK};font-weight:bold;font-size:10px;border:none;")
        self._alarm_desc = QLabel("Semua parameter normal.")
        self._alarm_desc.setWordWrap(True)
        self._alarm_desc.setStyleSheet(f"color:{T.FG2};font-size:9px;border:none;")
        af.addWidget(self._alarm_title)
        af.addWidget(self._alarm_desc)
        vb.addWidget(self._alarm_frame)

        vb.addStretch()

        sa = QScrollArea()
        sa.setWidget(sc); sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setStyleSheet("""
            QScrollArea{border:none;background:transparent;}
            QScrollArea>QWidget>QWidget{background:transparent;}
            QScrollBar:vertical{border:none;background:#0d1117;width:4px;}
            QScrollBar::handle:vertical{background:#333;min-height:20px;border-radius:2px;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}
        """)
        parent.addWidget(sa, 1)  # 25% width

    # ─────────────────────────────────────── Footer ──

    def _mk_footer(self, root):
        ft = QFrame()
        ft.setFixedHeight(50)
        ft.setStyleSheet(
            f"QFrame{{background:{T.PANEL};border-top:1px solid {T.BORDER};}}"
        )
        hl = QHBoxLayout(ft)
        hl.setContentsMargins(14, 0, 14, 0)
        hl.setSpacing(0)

        # Left: port / baud info
        left = QWidget(); left.setStyleSheet("background:transparent;")
        ll = QVBoxLayout(left)
        ll.setSpacing(2); ll.setContentsMargins(0, 0, 0, 0)
        ll.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        port_lbl = QLabel(f"USB  {config.modbus.port}")
        port_lbl.setStyleSheet(
            f"color:{T.FG2};font-size:11px;font-weight:bold;border:none;"
        )
        baud_lbl = QLabel(f"Baud {config.modbus.baudrate}  |  AQMS v2.0")
        baud_lbl.setStyleSheet(f"color:{T.FG3};font-size:9px;border:none;")
        ll.addWidget(port_lbl)
        ll.addWidget(baud_lbl)
        hl.addWidget(left)

        hl.addStretch()

        # Center: 4 stat blocks (label + value, 2 rows)
        def stat(label, init_val, color=T.FG1):
            w = QWidget(); w.setStyleSheet("background:transparent;")
            vb = QVBoxLayout(w)
            vb.setSpacing(1); vb.setContentsMargins(18, 5, 18, 5)
            vb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb = QLabel(label.upper())
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setStyleSheet(
                f"color:{T.FG3};font-size:8px;letter-spacing:0.5px;border:none;"
            )
            lv = QLabel(init_val)
            lv.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lv.setStyleSheet(
                f"color:{color};font-weight:bold;font-size:15px;"
                f"font-family:'{T.MONO}';border:none;"
            )
            vb.addWidget(lb); vb.addWidget(lv)
            return w, lv

        def vsep():
            """Thin vertical divider between stat blocks."""
            c = QWidget(); c.setStyleSheet("background:transparent;")
            ch = QHBoxLayout(c); ch.setContentsMargins(0, 8, 0, 8)
            line = QFrame(); line.setFrameShape(QFrame.Shape.VLine)
            line.setFixedWidth(1)
            line.setStyleSheet(f"background:{T.BORDER};border:none;")
            ch.addWidget(line)
            return c

        u_blk, self._f_uptime = stat("Uptime",    "0h 0m",  T.OK)
        s_blk, self._f_sent   = stat("Terkirim",  "0 data", T.BLUE)
        a_blk, self._f_alarms = stat("Alarm",     "0",      T.FG1)
        k_blk, _              = stat("Kepatuhan", "94.2%",  T.OK)

        for i, w in enumerate([u_blk, s_blk, a_blk, k_blk]):
            if i > 0:
                hl.addWidget(vsep())
            hl.addWidget(w)

        hl.addStretch()

        # Right: online / offline pill
        right = QWidget(); right.setStyleSheet("background:transparent;")
        rl = QVBoxLayout(right)
        rl.setSpacing(2); rl.setContentsMargins(0, 0, 0, 0)
        rl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self._ft_conn = QLabel("● ONLINE")
        self._ft_conn.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._ft_conn.setStyleSheet(
            f"color:{T.OK};font-size:10px;font-weight:bold;border:none;"
        )
        brand = QLabel("© Mitra Mutiara")
        brand.setAlignment(Qt.AlignmentFlag.AlignRight)
        brand.setStyleSheet(f"color:{T.FG3};font-size:9px;border:none;")
        rl.addWidget(self._ft_conn)
        rl.addWidget(brand)
        hl.addWidget(right)

        root.addWidget(ft)

        self._upt = QTimer()
        self._upt.timeout.connect(self._uptime)
        self._upt.start(60000)

    def _uptime(self):
        d = datetime.now() - self._t0
        h, m = d.seconds // 3600, (d.seconds % 3600) // 60
        self._f_uptime.setText(
            f"{d.days}d {h}h {m}m" if d.days else f"{h}h {m}m"
        )

    # ─────────────────────────────────────── Slots ──

    def _simulate(self):
        if self._real: return
        self.cards["pH"].update_value(random.uniform(6.8, 7.5))
        self.cards["TSS"].update_value(random.uniform(45, 65))
        self.cards["DEBIT"].update_value(random.uniform(85, 115))
        self.cards["COD"].update_value(random.uniform(80, 120))
        self.cards["NH3-N"].update_value(random.uniform(2, 5))

    def _on_sensor(self, data: SensorData):
        self._real = True
        self.cards["pH"].update_value(data.ph)
        self.cards["TSS"].update_value(data.tss)
        self.cards["DEBIT"].update_value(data.debit)
        self.cards["COD"].update_value(data.cod)
        self.cards["NH3-N"].update_value(data.nh3n)

        # Modbus LEDs → green
        for lb in self._leds.values():
            lb.setStyleSheet(
                f"background:{T.OK}20;color:{T.OK};"
                f"border:1px solid {T.OK};border-radius:3px;"
                f"font-size:9px;padding:2px;"
            )
        ts = datetime.fromtimestamp(data.timestamp).strftime("%H:%M:%S")
        self._mb_last.setText(ts)
        self._mb_stat.setText("Running")
        self._mb_stat.setStyleSheet(f"color:{T.OK};font-size:10px;font-weight:bold;border:none;")

        self._v_volt.setText(f"{data.voltage:.2f} V")
        self._v_curr.setText(f"{data.current:.2f} A")
        self._v_power.setText(f"{data.voltage*data.current:.2f} W")

        self._check_alarms(data)

    def _on_conn(self, ok: bool):
        txt = "Connected" if ok else "Disconnected"
        col = T.OK if ok else T.OFF
        for lb in (self._mm_conn, self._kl_conn):
            lb.setText(txt)
            lb.setStyleSheet(f"color:{col};font-size:10px;font-weight:bold;border:none;")
        # Footer pill
        self._ft_conn.setText("● ONLINE" if ok else "○ OFFLINE")
        self._ft_conn.setStyleSheet(
            f"color:{T.OK if ok else T.OFF};font-size:10px;font-weight:bold;border:none;"
        )

    def _on_count(self, cur, mx):
        self._buf_lbl.setText(f"{cur} records")
        self._buf_bar.setValue(int(cur/mx*100) if mx else 0)

    def _on_daily(self, n):
        self._f_sent.setText(f"{n} data")

    def _check_alarms(self, d: SensorData):
        msgs = []
        if not (6 <= d.ph <= 9):  msgs.append(f"pH {d.ph:.2f} di luar batas (6–9)")
        if d.tss > 200:           msgs.append(f"TSS {d.tss:.1f} mg/L > 200")
        if d.cod > 300:           msgs.append(f"COD {d.cod:.1f} mg/L > 300")
        if d.nh3n > 10:           msgs.append(f"NH3-N {d.nh3n:.1f} mg/L > 10")
        n = len(msgs)
        self._f_alarms.setText(str(n))
        if n:
            self._f_alarms.setStyleSheet(
                f"color:{T.ERR};font-weight:bold;font-size:15px;"
                f"font-family:'{T.MONO}';border:none;"
            )
            self._alarm_frame.setStyleSheet(
                f"background:{T.ERR}18;border:1px solid {T.ERR};border-radius:5px;")
            self._alarm_title.setText(f"⚠ ALARM: {n} parameter")
            self._alarm_title.setStyleSheet(f"color:{T.ERR};font-weight:bold;font-size:10px;border:none;")
            self._alarm_desc.setText("\n".join(msgs))
        else:
            self._f_alarms.setStyleSheet(
                f"color:{T.FG1};font-weight:bold;font-size:15px;"
                f"font-family:'{T.MONO}';border:none;"
            )
            self._alarm_frame.setStyleSheet(
                f"background:{T.OK}18;border:1px solid {T.OK};border-radius:5px;")
            self._alarm_title.setText("✓ Tidak ada alarm")
            self._alarm_title.setStyleSheet(f"color:{T.OK};font-weight:bold;font-size:10px;border:none;")
            self._alarm_desc.setText("Semua parameter dalam batas normal.")

    def refresh_sidebar(self):
        """Update sidebar setelah config berubah melalui SettingsDialog."""
        self._kl_uid.setText(config.server.uid_2)
        self._off_ph.setText(f"{config.offsets.ph_offset:+.2f}")
        self._off_tss.setText(f"{config.offsets.tss_offset:+.2f} mg/L")
        self._off_dbt.setText(f"{config.offsets.debit_offset:+.2f} m³/j")

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
