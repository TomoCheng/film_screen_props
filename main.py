"""仿 Windows 檔案複製對話框（無邊框、可自訂 config / 語系）。

結構總覽：
    Config           讀取 config.json 與 localization.json，集中管理所有常數。
    noise            分形雜訊，產生固定且平滑起伏的速率地形。
    progress_warp    進度重映射曲線（開頭快速衝到 3%，總時長不變）。
    widgets          手繪元件：IconButton / TitleIcon / SpeedGraph / ToggleButton。
    FakeCopyDialog   主視窗，組裝 UI 並以計時器推進模擬進度。
"""

import json
import math
import os
import random
import sys

from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# =====================================================================
# 設定與語系
# =====================================================================
class Config:
    """集中載入 config.json / localization.json，並提供型別化的常數。"""

    CONFIG_FILE = "config.json"
    LOCALIZATION_FILE = "localization.json"

    DEFAULTS = {
        "total_items": 26792,
        "total_size_gb": 5.0,
        "speed_base": 80.0,
        "src_name": "本機磁碟 (C:)",
        "dst_name": "抽取式磁碟 (E:)",
        "file_list_path": "files.txt",
        "language": "zh_TW",
        # 更新頻率（毫秒）
        "ui_update_interval_ms": 100,     # 進度 / 圖表 / 速率
        "time_update_interval_ms": 1000,  # 剩餘時間文字
        # 完成後自動關閉延遲（毫秒），0 = 不自動關閉
        "close_delay_ms": 3000,
        # 開頭加速：讓顯示進度在 fast_boost_secs 秒內衝到 fast_boost_target
        "fast_boost_secs": 10.0,
        "fast_boost_target": 0.03,
        # 速率圖表用色
        "graph_colors": {
            "background": "#ffffff",  # 底色
            "grid":       "#e2f0d9",  # 網格線
            "fill_upper": "#a9d08e",  # 曲線上方淺色填充
            "fill_lower": "#00b050",  # 曲線下方深色填充
            "curve_line": "#107c10",  # 曲線本身
            "baseline":   "#222222",  # 目前速率基準線
            "speed_text": "#000000",  # 速率文字
            "border":     "#bbbbbb",  # 外框
        },
        "key_0_remaining_sec": 23,
        "key_1_percent": 0,
        "key_2_percent": 20,
        "key_3_percent": 40,
        "key_4_percent": 60,
        "key_5_percent": 80,
    }

    DEFAULT_LANG = {
        "speed_label": "{speed} MB/秒",
        "completed_status": "已完成 {percent}%",
        "copying_text": "正在複製 {total_items} 個項目，從「{src}」到「{dst}」",
        "seconds": "約 {secs} 秒",
        "minutes_seconds": "約 {mins} 分 {secs} 秒",
        "hours_minutes": "約 {hours} 小時 {mins} 分",
        "completed_title": "已完成複製",
        "completed_header": "所有項目皆已複製完成",
        "paused": "已暫停",
        "less_details": "較少詳細資料",
        "more_details": "更多詳細資料",
        # 詳細資料區：標籤（灰）與值（黑）分開，方便個別上色
        "name_key": "名稱：",
        "time_key": "剩餘時間：",
        "items_key": "剩餘項目：",
        "items_value": "{items_left} 個（{size_left} GB）",
        "items_zero_value": "剩餘的項目: 0 (0.00 GB)",
        "time_zero": "0 秒",
    }

    DEFAULT_FILES = [
        "DSC_0192.JPG",
        "報告_final_v3.docx",
        "setup_x64.msi",
        "backup_2024.zip",
        "movie_trailer.mp4",
    ]

    def __init__(self):
        raw = dict(self.DEFAULTS)
        raw = self._deep_merge(raw, self._load_json(self.CONFIG_FILE))

        # 基本數值
        self.total_items = int(raw["total_items"])
        self.total_size_gb = float(raw["total_size_gb"])
        self.total_size_mb = self.total_size_gb * 1024
        self.speed_base = float(raw["speed_base"])
        self.src_name = raw["src_name"]
        self.dst_name = raw["dst_name"]
        self.language = raw.get("language", "zh_TW")

        # 計時
        self.ui_tick_ms = int(raw["ui_update_interval_ms"])
        self.time_tick_ms = int(raw["time_update_interval_ms"])
        self.close_delay_ms = int(raw["close_delay_ms"])

        # 開頭加速曲線
        self.fast_boost_secs = float(raw["fast_boost_secs"])
        self.fast_boost_target = float(raw["fast_boost_target"])

        # 圖表用色
        self.graph_colors = {**self.DEFAULTS["graph_colors"], **raw.get("graph_colors", {})}

        # 語系
        self.lang = self._load_language(raw["file_list_path"], raw["language"])

        # 假檔名清單
        self.fake_files = self._load_files(raw["file_list_path"])

        self.key_0_remaining_sec = raw["key_0_remaining_sec"]
        self.key_percent = {1:raw["key_1_percent"], 2:raw["key_2_percent"], 3:raw["key_3_percent"], 4:raw["key_4_percent"], 5:raw["key_5_percent"]}

    # -- 載入輔助 ------------------------------------------------------
    @staticmethod
    def _load_json(path):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    @staticmethod
    def _deep_merge(base, override):
        """僅對 dict 值做一層深合併（例如 graph_colors）。"""
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = {**base[k], **v}
            else:
                base[k] = v
        return base

    def _load_language(self, _file_list_path, lang_code):
        data = dict(self.DEFAULT_LANG)
        loc_db = self._load_json(self.LOCALIZATION_FILE)
        if loc_db:
            data.update(loc_db.get(lang_code, loc_db.get("zh_TW", {})))
        return data

    def _load_files(self, file_list_path):
        if os.path.exists(file_list_path):
            with open(file_list_path, "r", encoding="utf-8") as f:
                loaded = [line.strip() for line in f if line.strip()]
                if loaded:
                    return loaded
        return list(self.DEFAULT_FILES)


CFG = Config()

# 圖表座標邊界（速率映射到 Y 軸用）
SPEED_MIN = 0.0
SPEED_MAX = 120.0
GRAPH_POINTS = 150

# 視窗尺寸
WINDOW_WIDTH = 490
SHADOW_MARGIN = 12  # 四周留給陰影的空間


# =====================================================================
# 分形雜訊（固定、平滑起伏的速率地形）
# =====================================================================
def _hash_1d(x: int) -> float:
    x = (x << 13) ^ x
    return 1.0 - ((x * (x * x * 15731 + 789221) + 1376312589) & 0x7FFFFFFF) / 1073741824.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _fade(t: float) -> float:
    return t * t * t * (t * (t * 6 - 15) + 10)


def _value_noise(x: float) -> float:
    xi = math.floor(x)
    t = x - xi
    return _lerp(_hash_1d(xi & 0xFFFFFFFF), _hash_1d((xi + 1) & 0xFFFFFFFF), _fade(t))


def base_topology(x_val: float) -> float:
    """以 fBm 計算固定基底速率（同一個 x 永遠得到同樣的值）。"""
    sample = x_val * 0.12
    total, amp, freq = 0.0, 1.0, 1.0
    for _ in range(4):
        total += _value_noise(sample * freq) * amp
        amp *= 0.45
        freq *= 2.20
    speed = CFG.speed_base + total * (CFG.speed_base * 0.15)
    return max(10.0, min(115.0, speed))


# =====================================================================
# 進度重映射曲線
# =====================================================================
class ProgressWarp:
    """把「等速推進的內部進度 raw」重映射成「顯示進度 display」。

    需求：一開始 fast_secs 秒內，顯示進度就衝到 target（例如 3%），
    之後放慢、線性補完剩下的進度，總時長維持不變。

    做法：內部 raw 仍等速 0→1（所以總時長不變）。以兩段線性映射：
        raw ∈ [0, t_fast]  → display ∈ [0, target]   （開頭很快）
        raw ∈ [t_fast, 1]  → display ∈ [target, 1]   （之後放慢）
    其中 t_fast = fast_secs / 總時長，代表 fast_secs 秒對應的 raw 值。
    """

    def __init__(self, total_secs: float, fast_secs: float, target: float):
        # 邊界保護：fast_secs 不可超過總時長、target 落在 (0,1)
        self.target = min(max(target, 0.0001), 0.9999)
        safe_fast = min(max(fast_secs, 0.1), total_secs * 0.99)
        self.t_fast = safe_fast / total_secs if total_secs > 0 else 0.01

    def display(self, raw: float) -> float:
        raw = min(max(raw, 0.0), 1.0)
        if raw <= self.t_fast:
            return self.target * (raw / self.t_fast)
        return self.target + (1 - self.target) * ((raw - self.t_fast) / (1 - self.t_fast))


# =====================================================================
# HTML 小工具（RichText 上色）
# =====================================================================
def header_html(total_items: str, src: str, dst: str) -> str:
    """header 文字，來源 / 目的地上藍色（仿 Windows 超連結樣式）。"""
    text = CFG.lang["copying_text"].format(total_items=total_items, src="\0SRC\0", dst="\0DST\0")
    blue = '<span style="color:#0078d4;">{}</span>'
    return text.replace("\0SRC\0", blue.format(src)).replace("\0DST\0", blue.format(dst))


def detail_html(label: str, value: str) -> str:
    """詳細資料行：標籤灰色、值黑色。"""
    return f'<span style="color:#767676;">{label}</span>{value}'


# =====================================================================
# 手繪元件
# =====================================================================
class IconButton(QPushButton):
    """手繪幾何圖示按鈕（標題列與暫停/取消鈕共用）。"""

    def __init__(self, kind, parent=None, size=(46, 32),
                 hover_bg="#e9e9e9", hover_fg=None, rounded=False, pen_width=1.1):
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self._hover_bg = QColor(hover_bg)
        self._hover_fg = QColor(hover_fg) if hover_fg else None
        self._rounded = rounded
        self._pen_width = pen_width
        self.setFixedSize(*size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def set_kind(self, kind):
        self.kind = kind
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._hover_bg)
            if self._rounded:
                p.drawRoundedRect(QRectF(0, 0, w, h), 5, 5)
            else:
                p.fillRect(0, 0, w, h, self._hover_bg)

        fg = self._hover_fg if (self._hover and self._hover_fg) else QColor("#1b1b1b")
        cx, cy = w / 2.0, h / 2.0
        pen = QPen(fg, self._pen_width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)

        if self.kind == "minimize":
            p.setPen(pen)
            p.drawLine(int(cx - 5), round(cy), int(cx + 5), round(cy))

        elif self.kind == "maximize":
            p.setPen(pen)
            p.drawRect(QRectF(cx - 5, cy - 5, 10, 10))

        elif self.kind in ("close", "cancel"):
            p.setPen(pen)
            d = 5
            p.drawLine(int(cx - d), int(cy - d), int(cx + d), int(cy + d))
            p.drawLine(int(cx - d), int(cy + d), int(cx + d), int(cy - d))

        elif self.kind == "pause":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fg))
            bw, bh, gap = 2.4, 11, 3.2
            p.drawRoundedRect(QRectF(cx - gap / 2 - bw, cy - bh / 2, bw, bh), 0.6, 0.6)
            p.drawRoundedRect(QRectF(cx + gap / 2, cy - bh / 2, bw, bh), 0.6, 0.6)

        elif self.kind == "play":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fg))
            path = QPainterPath()
            path.moveTo(cx - 4, cy - 6)
            path.lineTo(cx + 6, cy)
            path.lineTo(cx - 4, cy + 6)
            path.closeSubpath()
            p.drawPath(path)

        p.end()


class TitleIcon(QWidget):
    """標題列左側手繪時鐘（鬧鐘意象）。手繪以避免 emoji 跨系統樣式不一致。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 32)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy, r = 12.0, 16.0, 7.0

        p.setPen(QPen(QColor("#4a4a4a"), 1.4))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

        # 頂端兩個小鈴鐺
        p.setPen(QPen(QColor("#4a4a4a"), 1.2))
        p.setBrush(QBrush(QColor("#4a4a4a")))
        p.drawEllipse(QRectF(cx - r + 0.5, cy - r - 1.5, 3, 3))
        p.drawEllipse(QRectF(cx + r - 3.5, cy - r - 1.5, 3, 3))

        # 指針
        hand = QPen(QColor("#333333"), 1.3)
        hand.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(hand)
        p.drawLine(int(cx), int(cy), int(cx - 3), int(cy - 2))
        p.drawLine(int(cx), int(cy), int(cx + 2), int(cy - 4))
        p.end()


class ToggleButton(QPushButton):
    """頁尾「較少詳細資料」樣式鈕（保留外觀，點擊不做事）。

    左側手繪 chevron + 文字，hover 只變色、不加底線。
    """

    def __init__(self, text, expanded=True, parent=None):
        super().__init__(parent)
        self._text = text
        self._expanded = expanded
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.setFixedHeight(20)
        fm = QFontMetrics(self._font())
        self.setMinimumWidth(18 + fm.horizontalAdvance(text) + 4)

    def _font(self):
        f = self.font()
        f.setFamily("Microsoft JhengHei")
        f.setPointSize(9)
        return f

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        color = QColor("#005a9e") if self._hover else QColor("#0067c0")

        cx, cy, aw = 8.0, h / 2.0, 4.0
        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if self._expanded:  # 向上 chevron
            p.drawLine(int(cx - aw), int(cy + 2), int(cx), int(cy - 2))
            p.drawLine(int(cx), int(cy - 2), int(cx + aw), int(cy + 2))
        else:               # 向下 chevron
            p.drawLine(int(cx - aw), int(cy - 2), int(cx), int(cy + 2))
            p.drawLine(int(cx), int(cy + 2), int(cx + aw), int(cy - 2))

        p.setFont(self._font())
        p.setPen(color)
        p.drawText(18, 0, self.width() - 18, h,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text)
        p.end()


class SpeedGraph(QWidget):
    """速率曲線圖：固定地形 + 進度遮罩 + 隨速率上下浮動的基準線與數字。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.alpha = 0.0
        self.current_speed = CFG.speed_base
        self.setFixedHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def update_graph(self, alpha, current_speed):
        self.alpha = alpha
        self.current_speed = current_speed
        self.update()

    @staticmethod
    def _speed_to_y(speed, h):
        return int(h - ((speed - SPEED_MIN) / (SPEED_MAX - SPEED_MIN)) * (h - 8) - 4)

    def paintEvent(self, event):
        c = CFG.graph_colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 地形取樣點
        pts = [
            (int(i * w / (GRAPH_POINTS - 1)), self._speed_to_y(base_topology(float(i)), h))
            for i in range(GRAPH_POINTS)
        ]

        # 背景 + 網格
        p.fillRect(0, 0, w, h, QColor(c["background"]))
        p.setPen(QPen(QColor(c["grid"]), 1))
        for i in range(1, 10):
            x = int(w * i / 10)
            p.drawLine(x, 0, x, h)
        for i in range(1, 4):
            y = int(h * i / 4)
            p.drawLine(0, y, w, y)

        # 曲線與填充（用進度遮罩裁切）
        progress_w = int(w * self.alpha)
        if len(pts) >= 2 and progress_w > 0:
            curve = QPainterPath()
            curve.moveTo(*pts[0])
            for i in range(1, len(pts)):
                x0, y0 = pts[i - 1]
                x1, y1 = pts[i]
                mx = x0 + (x1 - x0) / 2
                curve.cubicTo(mx, y0, mx, y1, x1, y1)

            p.save()
            p.setClipRect(0, 0, progress_w, h)

            lower = QPainterPath(curve)
            lower.lineTo(w, h)
            lower.lineTo(0, h)
            lower.closeSubpath()
            upper = QPainterPath()
            upper.addRect(0, 0, w, h)
            upper = upper.subtracted(lower)

            p.fillPath(upper, QBrush(QColor(c["fill_upper"])))
            p.fillPath(lower, QBrush(QColor(c["fill_lower"])))
            p.setPen(QPen(QColor(c["curve_line"]), 1.5))
            p.drawPath(curve)
            p.restore()

        # 目前速率基準線（隨速率上下浮動）
        base_y = max(15, min(self._speed_to_y(self.current_speed, h), h - 10))
        p.setPen(QPen(QColor(c["baseline"]), 1))
        p.drawLine(0, base_y, w, base_y)

        # 速率文字（浮在基準線上方一點，跟著一起跑）
        txt = CFG.lang["speed_label"].format(speed=f"{self.current_speed:.1f}")
        font = p.font()
        font.setFamily("Microsoft JhengHei")
        font.setPointSize(9)
        p.setFont(font)
        fm = QFontMetrics(font)
        tx = w - fm.horizontalAdvance(txt) - 15
        ty = max(2, min(base_y - fm.height() - 3, h - fm.height() - 2))
        p.setPen(QColor(c["speed_text"]))
        p.drawText(tx, ty + fm.ascent(), txt)

        # 外框
        p.setPen(QPen(QColor(c["border"]), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        p.end()


# =====================================================================
# 主視窗
# =====================================================================
class FakeCopyDialog(QWidget):
    """無邊框、仿 Windows 檔案複製對話框。"""

    FONT = "Microsoft JhengHei"

    def __init__(self):
        super().__init__()
        # 進度狀態
        self.raw_alpha = 0.0     # 內部等速進度 0..1（決定總時長）
        self.display_alpha = 0.0  # 顯示進度（經 warp 映射）
        self.current_speed = CFG.speed_base
        self.current_file = random.choice(CFG.fake_files) if CFG.fake_files else ""
        self.paused = False
        self._completed = False
        self._frame = 0.0
        self._last_time_str = ""
        self._drag_pos = None

        # 進度重映射曲線
        total_secs = CFG.total_size_mb / CFG.speed_base if CFG.speed_base else 1.0
        self.warp = ProgressWarp(total_secs, CFG.fast_boost_secs, CFG.fast_boost_target)

        self._setup_window()
        self._build_ui()
        self._start_timers()
        self._refresh_progress_widgets()
        self._refresh_time_label()

        # 套用固定尺寸
        self.setFixedWidth(WINDOW_WIDTH)
        self.layout().activate()
        self.setFixedSize(WINDOW_WIDTH, self.layout().totalSizeHint().height())

    # -- 視窗設定 ------------------------------------------------------
    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- UI 組裝 -------------------------------------------------------
    def _label(self, text="", size=13, color="#000000", rich=False, wrap=False):
        lbl = QLabel(text)
        if rich:
            lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(wrap)
        lbl.setStyleSheet(
            f"color: {color}; font-size: {size}px; font-family: '{self.FONT}', sans-serif;"
        )
        return lbl

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)

        content = QWidget()
        content.setObjectName("WindowContent")
        content.setStyleSheet(
            "#WindowContent { background-color: #ffffff; border: 1px solid #d0d0d0;"
            " border-radius: 8px; }"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setColor(QColor(0, 0, 0, 70))
        shadow.setOffset(0, 3)
        content.setGraphicsEffect(shadow)

        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_title_bar())
        root.addWidget(self._build_body())
        root.addWidget(self._build_footer())

        main_layout.addWidget(content)

    def _build_title_bar(self):
        bar = QFrame()
        bar.setStyleSheet(
            "background-color: #fbf5f5;"
            "border-top-left-radius: 7px; border-top-right-radius: 7px;"
        )
        bar.setFixedHeight(32)
        self.title_bar = bar

        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 0, 0, 0)
        row.setSpacing(0)

        row.addWidget(TitleIcon())
        self.title_label = self._label(
            CFG.lang["completed_status"].format(percent=0), size=12, color="#333333"
        )
        self.title_label.setStyleSheet(self.title_label.styleSheet() + " padding-left: 2px;")
        row.addWidget(self.title_label)
        row.addStretch()

        self.min_btn = IconButton("minimize", hover_bg="#e5e5e5")
        self.max_btn = IconButton("maximize", hover_bg="#e5e5e5")
        self.close_btn = IconButton("close", hover_bg="#e81123", hover_fg="#ffffff")
        self.min_btn.clicked.connect(self.showMinimized)
        self.close_btn.clicked.connect(QApplication.quit)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            row.addWidget(b)
        return bar

    def _build_body(self):
        body = QWidget()
        body.setStyleSheet(
            "background-color: #ffffff;"
            "border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;"
        )
        col = QVBoxLayout(body)
        col.setContentsMargins(20, 16, 20, 0)
        col.setSpacing(8)

        # header（來源/目的地藍字）
        self.header_label = self._label(rich=True, wrap=True)
        self.header_label.setText(header_html(f"{CFG.total_items:,}", CFG.src_name, CFG.dst_name))
        col.addWidget(self.header_label)

        # 大字百分比 + 暫停/取消
        pct_row = QHBoxLayout()
        pct_row.setContentsMargins(0, 0, 0, 0)
        self.pct_label = self._label(
            CFG.lang["completed_status"].format(percent=0), size=19
        )
        pct_row.addWidget(self.pct_label)
        pct_row.addStretch()

        self.pause_btn = IconButton("pause", size=(32, 30), hover_bg="#eaeaea",
                                    rounded=True, pen_width=1.2)
        self.cancel_btn = IconButton("cancel", size=(32, 30), hover_bg="#eaeaea",
                                     rounded=True, pen_width=1.2)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_btn.clicked.connect(QApplication.quit)
        pct_row.addWidget(self.pause_btn)
        pct_row.addWidget(self.cancel_btn)
        col.addLayout(pct_row)

        # 速率圖表
        graph_wrap = QVBoxLayout()
        graph_wrap.setContentsMargins(0, 6, 0, 6)
        self.graph = SpeedGraph()
        graph_wrap.addWidget(self.graph)
        col.addLayout(graph_wrap)

        # 詳細資料（名稱 / 時間 / 項目）
        details = QVBoxLayout()
        details.setContentsMargins(0, 6, 0, 12)
        details.setSpacing(5)
        self.name_label = self._label(size=12, color="#222222", rich=True, wrap=True)
        self.time_label = self._label(size=12, color="#222222", rich=True, wrap=True)
        self.items_label = self._label(size=12, color="#222222", rich=True, wrap=True)
        for lbl in (self.name_label, self.time_label, self.items_label):
            details.addWidget(lbl)
        col.addLayout(details)

        return body

    def _build_footer(self):
        footer = QFrame()
        footer.setStyleSheet(
            "QFrame { background-color: #f3f3f3; border-top: 1px solid #e5e5e5;"
            " border-bottom-left-radius: 7px; border-bottom-right-radius: 7px; }"
        )
        row = QHBoxLayout(footer)
        row.setContentsMargins(16, 8, 16, 8)
        # 保留外觀，點擊不綁任何行為
        self.toggle_btn = ToggleButton(CFG.lang["less_details"], expanded=True)
        row.addWidget(self.toggle_btn)
        row.addStretch()
        return footer

    # -- 計時器 --------------------------------------------------------
    def _start_timers(self):
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(CFG.ui_tick_ms)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

        self._time_timer = QTimer(self)
        self._time_timer.setInterval(CFG.time_tick_ms)
        self._time_timer.timeout.connect(self._refresh_time_label)
        self._time_timer.start()

    def _tick(self):
        if self.paused or self._completed:
            return

        self._frame += 1.0

        # 速率：固定地形 + 輕微抖動
        base = base_topology(self.raw_alpha * (GRAPH_POINTS - 1))
        jitter = math.sin(self._frame * 1.6) * 0.02 + math.cos(self._frame * 2.8) * 0.01
        if random.random() < 0.10:
            jitter += random.choice([-0.01, 0.01])
        self.current_speed = max(SPEED_MIN + 5.0, min(SPEED_MAX - 5.0, base * (1.0 + jitter)))

        # 內部進度等速推進（總時長由此決定，維持不變）
        d_raw = (self.current_speed * (CFG.ui_tick_ms / 1000.0)) / CFG.total_size_mb
        self.raw_alpha = min(self.raw_alpha + d_raw, 1.0)
        # 顯示進度 = warp 映射（開頭快速衝到 target）
        self.display_alpha = self.warp.display(self.raw_alpha)

        if CFG.fake_files and random.random() < 0.10:
            self.current_file = random.choice(CFG.fake_files)

        self._refresh_progress_widgets()

        if self.raw_alpha >= 1.0:
            self._on_completed()

    # -- 畫面更新 ------------------------------------------------------
    def _refresh_progress_widgets(self):
        """更新進度、圖表、速率、檔名、項目數（不含剩餘時間）。"""
        items_left = max(0, int(CFG.total_items * (1.0 - self.display_alpha)))
        remaining_gb = max(0.0, (CFG.total_size_mb * (1.0 - self.display_alpha)) / 1024)

        self.graph.update_graph(self.display_alpha, self.current_speed)

        pct_text = CFG.lang["completed_status"].format(percent=int(self.display_alpha * 100))
        self.pct_label.setText(pct_text)
        self.title_label.setText(pct_text)
        self.setWindowTitle(pct_text)

        self.name_label.setText(detail_html(CFG.lang["name_key"], self.current_file))
        self.items_label.setText(detail_html(
            CFG.lang["items_key"],
            CFG.lang["items_value"].format(items_left=f"{items_left:,}",
                                           size_left=f"{remaining_gb:.2f}")
        ))

    def _refresh_time_label(self):
        """更新剩餘時間文字（獨立於 UI tick，避免頻繁跳動）。"""
        if self.paused or self._completed:
            return
        secs = self._get_remaining_time(self.display_alpha)
        time_str = self._format_time(secs)
        if time_str != self._last_time_str:
            self._last_time_str = time_str
            self.time_label.setText(detail_html(CFG.lang["time_key"], time_str))

    def _get_remaining_time(self, alpha):
        speed = self.current_speed if self.current_speed > 0 else CFG.speed_base
        secs = CFG.total_size_mb * (1.0 - alpha) / speed
        return secs

    def _get_alpha_by_remaining_time(self, time):
        alpha = 1.0 - (time * CFG.speed_base) / CFG.total_size_mb
        return alpha

    @staticmethod
    def _format_time(secs):
        if secs < 60:
            return CFG.lang["seconds"].format(secs=max(0, int(secs)))
        if secs < 3600:
            return CFG.lang["minutes_seconds"].format(mins=int(secs // 60), secs=int(secs % 60))
        return CFG.lang["hours_minutes"].format(
            hours=int(secs // 3600), mins=int((secs % 3600) // 60)
        )

    def _on_completed(self):
        self._completed = True
        self._tick_timer.stop()
        self._time_timer.stop()

        # 先註冊自動關閉，確保後面就算 UI 更新丟例外也不會卡住不關
        if CFG.close_delay_ms > 0:
            QTimer.singleShot(CFG.close_delay_ms, QApplication.quit)

        self.display_alpha = 1.0
        self.graph.update_graph(1.0, self.current_speed)
        self.pct_label.setText(CFG.lang["completed_status"].format(percent=100))
        self.title_label.setText(CFG.lang["completed_title"])
        self.header_label.setText(CFG.lang["completed_header"])
        self.time_label.setText(detail_html(CFG.lang["time_key"], CFG.lang["time_zero"]))
        try:
            self.items_label.setText(
                detail_html(CFG.lang["items_key"], CFG.lang["items_zero_value"])
            )
        except KeyError:
            # 語系檔缺少 items_zero_value 時，退回顯示空字串而不是整個崩潰
            self.items_label.setText(detail_html(CFG.lang["items_key"], ""))

    # -- 互動 ----------------------------------------------------------
    def _toggle_pause(self):
        if self._completed:
            return
        self.paused = not self.paused
        self.pause_btn.set_kind("play" if self.paused else "pause")
        if self.paused:
            self.header_label.setText(CFG.lang["paused"])
        else:
            self.header_label.setText(
                header_html(f"{CFG.total_items:,}", CFG.src_name, CFG.dst_name)
            )

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        target = -1

        if key == Qt.Key.Key_0:
            target = self._get_alpha_by_remaining_time(CFG.key_0_remaining_sec)
        elif Qt.Key.Key_1 <= key <= Qt.Key.Key_5:
            target = (CFG.key_percent[key - Qt.Key.Key_1 + 1] / 100)
        

        if target != -1 and not self._completed:
            self.display_alpha = target
            # 反推 raw_alpha，讓後續推進銜接一致
            self.raw_alpha = self._invert_warp(self.display_alpha)
            self.current_speed = base_topology(self.raw_alpha * (GRAPH_POINTS - 1))
            if CFG.fake_files:
                self.current_file = random.choice(CFG.fake_files)
            self._refresh_progress_widgets()
            self._refresh_time_label()
        else:
            super().keyPressEvent(event)

    def _invert_warp(self, display):
        """由顯示進度反推內部 raw（keyPress 跳轉時用）。"""
        t = self.warp
        if display <= t.target:
            return t.t_fast * (display / t.target) if t.target else 0.0
        return t.t_fast + (1 - t.t_fast) * ((display - t.target) / (1 - t.target))

    # -- 拖曳（僅限標題列）--------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.position().toPoint().y() <= SHADOW_MARGIN + self.title_bar.height():
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


def main():
    app = QApplication(sys.argv)
    dlg = FakeCopyDialog()
    screen = app.primaryScreen().geometry()
    dlg.move((screen.width() - dlg.width()) // 2, (screen.height() - dlg.height()) // 2)
    dlg.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
