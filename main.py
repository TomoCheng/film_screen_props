import json
import os
import math
import random
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QKeyEvent,
    QFontMetrics,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
)

# Load core configurations
CONFIG_FILE = "config.json"
LOCALIZATION_FILE = "localization.json"

if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

TOTAL_ITEMS = config["total_items"]
TOTAL_SIZE_GB = config["total_size_gb"]
TOTAL_SIZE_MB = TOTAL_SIZE_GB * 1024
SPEED_BASE = config["speed_base"]
SRC_NAME = config["src_name"]
DST_NAME = config["dst_name"]
FILE_LIST_PATH = config["file_list_path"]
LANG_CODE = config.get("language", "zh_TW")
GRAPH_POINTS = 150  # 橫軸總點數，代表從 0% 到 100% 的固定地形

# 固定圖表邊界數值限制：底部為 0 MB/s，最高為 120 MB/s
SPEED_MIN_LIMIT = 0.0
SPEED_MAX_LIMIT = 120.0

# Load localization configurations
LOCALIZATION_DATA = {}
if os.path.exists(LOCALIZATION_FILE):
    with open(LOCALIZATION_FILE, "r", encoding="utf-8") as f:
        loc_db = json.load(f)
        LOCALIZATION_DATA = loc_db.get(LANG_CODE, loc_db.get("zh_TW", {}))

lang_data = LOCALIZATION_DATA

# Load raw list of mock filenames
if os.path.exists(FILE_LIST_PATH):
    with open(FILE_LIST_PATH, "r", encoding="utf-8") as f:
        FAKE_FILES = [line.strip() for line in f if line.strip()]


def hash_1d(x: int) -> float:
    """確定性偽隨機雜湊函數，返回 -1.0 到 1.0 之間的固定浮點數。"""
    x = (x << 13) ^ x
    return 1.0 - ((x * (x * x * 15731 + 789221) + 1376312589) & 0x7FFFFFFF) / 1073741824.0


def lerp(a: float, b: float, t: float) -> float:
    """線性插值"""
    return a + (b - a) * t


def fade(t: float) -> float:
    """平滑過渡權重函數"""
    return t * t * t * (t * (t * 6 - 15) + 10)


def value_noise_1d(x: float) -> float:
    """一維價值噪值"""
    x_floor = math.floor(x)
    x_cell = x_floor & 0xFFFFFFFF
    t = x - x_floor
    u = fade(t)
    
    v0 = hash_1d(x_cell)
    v1 = hash_1d(x_cell + 1)
    
    return lerp(v0, v1, u)


def get_base_topology(x_val: float) -> float:
    """
    利用分形布朗運動 (fBm) 計算固定基底波形速率。
    計算出的速率會自然波動，並確保基底分布在合理的區間。
    """
    sample_pos = x_val * 0.12
    total_noise = 0.0
    amplitude = 1.0
    frequency = 1.0
    
    for _ in range(4):
        total_noise += value_noise_1d(sample_pos * frequency) * amplitude
        amplitude *= 0.45
        frequency *= 2.20

    # 基底映射範圍：以 SPEED_BASE 為基準產生崎嶇波形起伏
    speed = SPEED_BASE + (total_noise * (SPEED_BASE * 0.15))
    return max(10.0, min(115.0, speed))


class SpeedGraph(QWidget):
    """Widget displaying the fixed real-time transfer speed curve with progression overlay."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_speed = SPEED_BASE
        self.alpha = 0.0
        self.setMinimumHeight(95)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    def update_graph(self, alpha: float, current_speed: float):
        self.alpha = alpha
        self.current_speed = current_speed
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        
        # 1. 計算整張圖表的 Y 軸座標位置（固定映射：最底為 0，最頂為 120）
        pts = []
        raw_speeds = []
        for i in range(GRAPH_POINTS):
            sp = get_base_topology(float(i))
            raw_speeds.append(sp)

        for i, sp in enumerate(raw_speeds):
            x = int(i * w / (GRAPH_POINTS - 1))
            # 依據規定的 0 到 120 MB/s 邊界等比例渲染 Y 軸
            y = int(h - ((sp - SPEED_MIN_LIMIT) / (SPEED_MAX_LIMIT - SPEED_MIN_LIMIT)) * (h - 8) - 4)
            pts.append((x, y))

        # =================================================================
        # 1. 第一層：背景網格
        # =================================================================
        painter.fillRect(0, 0, w, h, QColor("#ffffff"))
        
        grid_pen = QPen(QColor("#e2f0d9"), 1)
        painter.setPen(grid_pen)
        
        cols = 10
        for i in range(1, cols):
            x = int(w * i / cols)
            painter.drawLine(x, 0, x, h)
            
        rows = 4
        for i in range(1, rows):
            y = int(h * i / rows)
            painter.drawLine(0, y, w, y)

        # =================================================================
        # 2. 第二層：速率的圖表 (由進度遮罩控制)
        # =================================================================
        progress_width = int(w * self.alpha)
        
        if len(pts) >= 2 and progress_width > 0:
            curve_path = QPainterPath()
            curve_path.moveTo(pts[0][0], pts[0][1])
            for i in range(1, len(pts)):
                p0 = pts[i - 1]
                p1 = pts[i]
                cp1_x = p0[0] + (p1[0] - p0[0]) / 2
                cp1_y = p0[1]
                cp2_x = p0[0] + (p1[0] - p0[0]) / 2
                cp2_y = p1[1]
                curve_path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, p1[0], p1[1])

            painter.save()
            painter.setClipRect(0, 0, progress_width, h)

            # A. 填滿速率圖表背景淺色部分 (#a9d08e)
            upper_fill = QPainterPath()
            upper_fill.addRect(0, 0, w, h)
            
            lower_box = QPainterPath(curve_path)
            lower_box.lineTo(w, h)
            lower_box.lineTo(0, h)
            lower_box.closeSubpath()
            
            upper_fill = upper_fill.subtracted(lower_box)
            painter.fillPath(upper_fill, QBrush(QColor("#a9d08e")))

            # B. 填滿速率圖表速率深色部分 (#00b050)
            painter.fillPath(lower_box, QBrush(QColor("#00b050")))

            # C. 繪製深綠色主速率線
            line_pen = QPen(QColor("#107c10"), 1.5)
            painter.setPen(line_pen)
            painter.drawPath(curve_path)
            
            painter.restore()

        # =================================================================
        # 3. 第三層：基準線
        # =================================================================
        current_y = int(h - ((self.current_speed - SPEED_MIN_LIMIT) / (SPEED_MAX_LIMIT - SPEED_MIN_LIMIT)) * (h - 8) - 4)
        current_y = max(15, min(current_y, h - 10))

        current_line_pen = QPen(QColor("#222222"), 1)
        painter.setPen(current_line_pen)
        painter.drawLine(0, current_y, w, current_y)

        # =================================================================
        # 4. 第四層：速率文字 (浮在基準線的上面一點，透明背景)
        # =================================================================
        speed_text = lang_data["speed_label"].format(speed=f"{self.current_speed:.1f}")
        font = painter.font()
        font.setFamily("Microsoft JhengHei")
        font.setPointSize(9)
        painter.setFont(font)
        
        fm = QFontMetrics(font)
        text_width = fm.horizontalAdvance(speed_text)
        text_height = fm.height()

        text_x = w - text_width - 15
        text_y_pos = current_y - text_height - 3
        text_y_pos = max(2, min(text_y_pos, h - text_height - 2))

        painter.setPen(QColor("#000000"))
        painter.drawText(text_x, text_y_pos + fm.ascent(), speed_text)

        # 繪製外框
        border_pen = QPen(QColor("#bbbbbb"), 1)
        painter.setPen(border_pen)
        painter.drawRect(0, 0, w - 1, h - 1)
        painter.end()


class FakeCopyDialog(QWidget):
    """Custom frameless Windows-style file copying dialog."""

    def __init__(self):
        super().__init__()
        self.alpha = 0.0
        self.percent = 0.0
        self.items_left = TOTAL_ITEMS
        self.current_speed = SPEED_BASE
        self.paused = False
        self.current_file = random.choice(FAKE_FILES) if FAKE_FILES else ""
        self.details_visible = True
        self._drag_pos = None
        self._is_completed = False
        self._frame_counter = 0.0

        self._setup_window()
        self._build_ui()
        self._start_timers()
        
        self._update_ui_state()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(490)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)

        window_content = QWidget()
        window_content.setObjectName("WindowContent")
        window_content.setStyleSheet(
            """
            #WindowContent { 
                background-color: #ffffff; 
                border: 1px solid #d0d0d0;
                border-radius: 8px;
            }
        """
        )
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 60))
        shadow.setOffset(0, 3)
        window_content.setGraphicsEffect(shadow)

        root = QVBoxLayout(window_content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = self._make_title_bar()
        root.addWidget(self.title_bar)

        body = QWidget()
        body.setStyleSheet("background-color: #ffffff; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 16, 20, 0)
        body_layout.setSpacing(8)

        self.header_label = QLabel(
            lang_data["copying_text"].format(
                total_items=f"{TOTAL_ITEMS:,}", src=SRC_NAME, dst=DST_NAME
            )
        )
        self.header_label.setStyleSheet("color: #000000; font-size: 13px; font-family: 'Microsoft JhengHei', sans-serif;")
        body_layout.addWidget(self.header_label)

        pct_row = QHBoxLayout()
        self.pct_label = QLabel(lang_data["completed_status"].format(percent=0))
        self.pct_label.setStyleSheet(
            "color: #000000; font-size: 19px; font-family: 'Microsoft JhengHei', sans-serif;"
        )
        pct_row.addWidget(self.pct_label)
        pct_row.addStretch()

        self.pause_btn = QPushButton("⏸")
        self.cancel_btn = QPushButton("✕")
        for btn in (self.pause_btn, self.cancel_btn):
            btn.setFixedSize(30, 26)
            btn.setStyleSheet(
                """
                QPushButton {
                    background: transparent;
                    color: #333333;
                    font-size: 12px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #eaeaea;
                    border-radius: 3px;
                }
            """
            )
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_btn.clicked.connect(QApplication.quit)
        pct_row.addWidget(self.pause_btn)
        pct_row.addWidget(self.cancel_btn)
        body_layout.addLayout(pct_row)

        graph_container = QWidget()
        graph_container_layout = QVBoxLayout(graph_container)
        graph_container_layout.setContentsMargins(0, 6, 0, 6)
        
        self.graph = SpeedGraph()
        graph_container_layout.addWidget(self.graph)
        body_layout.addWidget(graph_container)

        self.details_frame = QFrame()
        self.details_frame.setStyleSheet("background-color: #ffffff;")
        det_layout = QVBoxLayout(self.details_frame)
        det_layout.setContentsMargins(0, 6, 0, 12)
        det_layout.setSpacing(5)

        self.name_label = QLabel(lang_data["name_label"].format(filename=self.current_file))
        self.time_label = QLabel(lang_data["time_label"].format(time_str=""))
        self.items_label = QLabel(lang_data["items_label"].format(items_left="", size_left=""))
        for lbl in (self.name_label, self.time_label, self.items_label):
            lbl.setStyleSheet("color: #222222; font-size: 12px; font-family: 'Microsoft JhengHei', sans-serif;")
            lbl.setWordWrap(True)
            det_layout.addWidget(lbl)

        body_layout.addWidget(self.details_frame)
        root.addWidget(body)

        footer = QFrame()
        footer.setStyleSheet(
            """
            QFrame {
                background-color: #f3f3f3; 
                border-top: 1px solid #e5e5e5;
                border-bottom-left-radius: 7px;
                border-bottom-right-radius: 7px;
            }
        """
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 8, 16, 8)

        self.toggle_btn = QPushButton(f"＾ {lang_data['less_details']}")
        self.toggle_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                color: #000000;
                font-size: 12px;
                font-family: 'Microsoft JhengHei', sans-serif;
                border: none;
                text-align: left;
            }
            QPushButton:hover { color: #0078d4; }
        """
        )
        self.toggle_btn.clicked.connect(self._toggle_details)
        footer_layout.addWidget(self.toggle_btn)
        footer_layout.addStretch()
        root.addWidget(footer)

        main_layout.addWidget(window_content)

    def _make_title_bar(self):
        bar = QFrame()
        bar.setStyleSheet("background-color: #fbf5f5; border-top-left-radius: 7px; border-top-right-radius: 7px;")
        bar.setFixedHeight(32)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)

        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent;")
        layout.addWidget(self.icon_label)

        self.title_label = QLabel(lang_data["completed_status"].format(percent=0))
        self.title_label.setStyleSheet("color: #333333; font-size: 12px; font-family: 'Microsoft JhengHei', sans-serif;")
        layout.addWidget(self.title_label)
        layout.addStretch()

        for sym, slot, is_close in [
            ("－", self._minimize, False),
            ("▢", None, False),
            ("✕", QApplication.quit, True),
        ]:
            btn = QPushButton(sym)
            btn.setFixedSize(46, 32)
            if is_close:
                btn.setStyleSheet(
                    """
                    QPushButton { background: transparent; color: #000000; font-size: 11px; border: none; }
                    QPushButton:hover { background-color: #e81123; color: #ffffff; border-top-right-radius: 7px; }
                """
                )
            else:
                btn.setStyleSheet(
                    """
                    QPushButton { background: transparent; color: #000000; font-size: 11px; border: none; }
                    QPushButton:hover { background-color: #eaeaea; }
                """
                )
            if slot:
                btn.clicked.connect(slot)
            layout.addWidget(btn)
        return bar

    def _start_timers(self):
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(100)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

        self._elapsed = 0.0

    def _tick(self):
        if self.paused or self._is_completed:
            return

        self._elapsed += 0.1
        self._frame_counter += 1.0

        # A. 取得目前百分比位置對應的固定分形噪值基準速率
        target_idx = self.alpha * (GRAPH_POINTS - 1)
        base_speed = get_base_topology(target_idx)

        # B. 疊加微觀的高頻平滑抖動限制在 +-3% 的微小真實範圍內
        jitter_percent = (math.sin(self._frame_counter * 1.6) * 0.02 + 
                          math.cos(self._frame_counter * 2.8) * 0.01)
        
        if random.random() < 0.10:
            jitter_percent += random.choice([-0.01, 0.01])

        # 最終輸出與圖表波形高度完美同步，並限制在絕對邊界內
        self.current_speed = base_speed * (1.0 + jitter_percent)
        self.current_speed = max(SPEED_MIN_LIMIT + 5.0, min(SPEED_MAX_LIMIT - 5.0, self.current_speed))

        # 進度核心疊加
        delta_alpha = (self.current_speed * 0.1) / TOTAL_SIZE_MB
        self.alpha = min(self.alpha + delta_alpha, 1.0)
        self.percent = self.alpha * 100.0

        if FAKE_FILES and random.random() < 0.10:
            self.current_file = random.choice(FAKE_FILES)

        self._update_ui_state()

    def _update_ui_state(self):
        self.items_left = max(0, int(TOTAL_ITEMS * (1.0 - self.alpha)))
        remaining_gb = max(0.0, (TOTAL_SIZE_MB * (1.0 - self.alpha)) / 1024)
        
        # 刷新固定 Y 軸 (0 ~ 120) 的網格圖表與基準線
        self.graph.update_graph(self.alpha, self.current_speed)

        # 剩餘時間計算
        speed_for_calc = self.current_speed if self.current_speed > 0 else SPEED_BASE
        remaining_mb = TOTAL_SIZE_MB * (1.0 - self.alpha)
        secs = remaining_mb / speed_for_calc

        if secs < 60:
            time_str = lang_data["seconds"].format(secs=max(0, int(secs)))
        elif secs < 3600:
            time_str = lang_data["minutes_seconds"].format(
                mins=int(secs // 60), secs=int(secs % 60)
            )
        else:
            time_str = lang_data["hours_minutes"].format(
                hours=int(secs // 3600), mins=int((secs % 3600) // 60)
            )

        # 全面同步更新界面組件
        pct_int = int(self.percent)
        self.pct_label.setText(lang_data["completed_status"].format(percent=pct_int))
        self.title_label.setText(lang_data["completed_status"].format(percent=pct_int))
        self.setWindowTitle(lang_data["completed_status"].format(percent=pct_int))
        self.name_label.setText(lang_data["name_label"].format(filename=self.current_file))
        self.time_label.setText(lang_data["time_label"].format(time_str=time_str))
        
        self.items_label.setText(
            lang_data["items_label"].format(
                items_left=f"{self.items_left:,}",
                size_left=f"{remaining_gb:.2f}",
            )
        )

        if self.alpha >= 1.0 and not self._is_completed:
            self._is_completed = True
            self._tick_timer.stop()
            
            self.pct_label.setText(lang_data["completed_status"].format(percent=100))
            self.title_label.setText(lang_data["completed_title"])
            self.header_label.setText(lang_data["completed_header"])
            self.time_label.setText(lang_data["remaining_zero"])
            self.items_label.setText(lang_data["items_zero"])
            
            QTimer.singleShot(1000, QApplication.quit)

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        target_percent = -1

        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            target_percent = (key - Qt.Key.Key_1 + 1) * 10
        elif key == Qt.Key.Key_0:
            target_percent = 100

        if target_percent != -1 and not self._is_completed:
            self.alpha = target_percent / 100.0
            self.percent = self.alpha * 100.0
            
            # 手動跳躍進度時，同步該點的基底分形波形速率，確保位置完全對齊
            target_idx = self.alpha * (GRAPH_POINTS - 1)
            self.current_speed = get_base_topology(target_idx)
            
            if FAKE_FILES:
                self.current_file = random.choice(FAKE_FILES)

            self._update_ui_state()
        else:
            super().keyPressEvent(event)

    def _toggle_pause(self):
        if self._is_completed:
            return
        self.paused = not self.paused
        self.pause_btn.setText("▶" if self.paused else "⏸")
        if self.paused:
            self.header_label.setText(lang_data["paused"])
        else:
            self.header_label.setText(
                lang_data["copying_text"].format(
                    total_items=f"{TOTAL_ITEMS:,}", src=SRC_NAME, dst=DST_NAME
                )
            )

    def _toggle_details(self):
        self.details_visible = not self.details_visible
        self.details_frame.setVisible(self.details_visible)
        
        arrow = "＾" if self.details_visible else "ˇ"
        self.toggle_btn.setText(f"{arrow} {lang_data['less_details'] if self.details_visible else lang_data['more_details']}")
        
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())

    def _minimize(self):
        self.showMinimized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dlg = FakeCopyDialog()

    screen = app.primaryScreen().geometry()
    dlg.adjustSize()
    dlg.move(
        (screen.width() - dlg.width()) // 2,
        (screen.height() - dlg.height()) // 2,
    )
    dlg.show()
    sys.exit(app.exec())