from datetime import datetime, timedelta
import os
import platform
import socket
import subprocess
import sys
import time

import NDIlib as ndi
import numpy as np
from PySide6.QtCore import QRect, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

# ============================================================================
# Industrial Broadcast QSS Stylesheet
# ============================================================================
INDUSTRIAL_STYLESHEET = """
QMainWindow {
    background-color: #0B0D10;
    color: #E0E6ED;
}

QToolBar {
    background-color: #12151A;
    border-bottom: 2px solid #222731;
    padding: 6px 12px;
    spacing: 10px;
}

QToolBar QLabel {
    color: #8C96A5;
    font-family: "Menlo", "Consolas", "Courier New", monospace;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 1px;
}

QStatusBar {
    background-color: #101216;
    color: #00E5FF;
    border-top: 1px solid #222731;
    font-family: "Menlo", "Consolas", monospace;
    font-size: 11px;
    padding: 2px 8px;
}

QSpinBox {
    background-color: #08090C;
    color: #00E5FF;
    border: 1px solid #2C333F;
    border-radius: 3px;
    padding: 4px 8px;
    font-family: "Menlo", "Consolas", monospace;
    font-size: 12px;
    font-weight: bold;
}

QSpinBox:focus {
    border: 1px solid #00E5FF;
}

QComboBox {
    background-color: #0B0D10;
    color: #D0D7DE;
    border: 1px solid #282E38;
    border-radius: 3px;
    padding: 5px 10px;
    font-family: "Menlo", "Consolas", monospace;
    font-size: 11px;
}

QComboBox:hover {
    border: 1px solid #3E4756;
    background-color: #101318;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #12151A;
    color: #D0D7DE;
    selection-background-color: #1F2937;
    selection-color: #00E5FF;
    border: 1px solid #3E4756;
}

QPushButton {
    background-color: #1B2028;
    color: #C9D1D9;
    border: 1px solid #303742;
    border-radius: 3px;
    padding: 5px 12px;
    font-family: "Menlo", "Consolas", monospace;
    font-size: 11px;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #252C37;
    border: 1px solid #485263;
    color: #FFFFFF;
}

QPushButton:pressed {
    background-color: #13171D;
}

QPushButton:disabled {
    background-color: #111317;
    color: #454C59;
    border: 1px solid #1C2026;
}
"""


# ============================================================================
# 1. Hardware GPU Encoder Detection Utility
# ============================================================================
def detect_best_h264_encoder():
    """Detects available hardware-accelerated H.264 encoders in FFmpeg."""
    try:
        output = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:
        return "libx264"

    system = platform.system()
    candidates = []

    if "h264_nvenc" in output:
        candidates.append("h264_nvenc")
    if "h264_qsv" in output:
        candidates.append("h264_qsv")
    if system == "Darwin" and "h264_videotoolbox" in output:
        candidates.append("h264_videotoolbox")
    if "h264_amf" in output:
        candidates.append("h264_amf")

    for enc in candidates:
        test_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=s=64x64:d=0.1",
            "-c:v",
            enc,
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(
            test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if res.returncode == 0:
            return enc

    return "libx264"


# ============================================================================
# 2. Segmented LED Stereo VU Meter Widget
# ============================================================================
class VUMeterWidget(QWidget):
    """Draws a broadcast-style segmented stereo LED audio meter with dB scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(38)
        self.setMinimumHeight(135)

        self.left_level = 0.0
        self.right_level = 0.0
        self.left_peak = 0.0
        self.right_peak = 0.0

        self.decay_timer = QTimer(self)
        self.decay_timer.timeout.connect(self._decay)
        self.decay_timer.start(33)

    @Slot(float, float)
    def update_levels(self, left: float, right: float):
        self.left_level = max(self.left_level, left)
        self.right_level = max(self.right_level, right)
        self.left_peak = max(self.left_peak, left)
        self.right_peak = max(self.right_peak, right)
        self.update()

    def _decay(self):
        decay_factor = 0.90
        self.left_level *= decay_factor
        self.right_level *= decay_factor
        self.left_peak = max(self.left_level, self.left_peak * 0.98)
        self.right_peak = max(self.right_level, self.right_peak * 0.98)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        w, h = self.width(), self.height()

        # Recessed Meter Housing
        painter.fillRect(0, 0, w, h, QColor(9, 11, 14))
        painter.setPen(QColor(32, 37, 46))
        painter.drawRect(0, 0, w - 1, h - 1)

        top_pad = 6
        bot_pad = 14
        usable_h = h - top_pad - bot_pad

        num_segments = 28
        seg_gap = 1
        seg_h = max(2, (usable_h - (num_segments - 1) * seg_gap) // num_segments)
        bar_w = 7

        # L & R Channel X coordinates
        x_left = 4
        x_right = 13

        channels = [
            (x_left, self.left_level, self.left_peak),
            (x_right, self.right_level, self.right_peak),
        ]

        for x, level, peak in channels:
            for s in range(num_segments):
                seg_ratio = (s + 1) / num_segments
                seg_y = top_pad + usable_h - (s + 1) * (seg_h + seg_gap)

                # Determine LED color zone (Green -> Amber -> Red)
                if seg_ratio > 0.90:  # > -6 dBFS (Clip zone)
                    on_color = QColor(255, 35, 35)
                    off_color = QColor(45, 14, 14)
                elif seg_ratio > 0.70:  # -18 to -6 dBFS (Nominal/Warning)
                    on_color = QColor(255, 190, 10)
                    off_color = QColor(42, 34, 10)
                else:  # < -18 dBFS (Safe zone)
                    on_color = QColor(0, 230, 100)
                    off_color = QColor(10, 38, 22)

                is_lit = level >= (s / num_segments)
                painter.fillRect(
                    x, seg_y, bar_w, seg_h, on_color if is_lit else off_color
                )

            # Peak hold segment indicator
            if peak > 0.03:
                peak_seg = min(
                    num_segments - 1, int(peak * num_segments)
                )
                peak_y = top_pad + usable_h - (peak_seg + 1) * (seg_h + seg_gap)
                painter.fillRect(x, peak_y, bar_w, seg_h, QColor(0, 229, 255))

        # Scale Labels on Right Side
        font = QFont("Menlo", 6, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(100, 110, 125))

        marks = [(1.0, "0"), (0.9, "6"), (0.7, "18"), (0.33, "40")]
        for ratio, label in marks:
            y_pos = top_pad + int((1.0 - ratio) * usable_h) + 3
            painter.drawText(22, y_pos, label)

        # Bottom L / R Legend
        painter.setPen(QColor(130, 145, 165))
        painter.drawText(x_left + 1, h - 3, "L")
        painter.drawText(x_right + 1, h - 3, "R")


# ============================================================================
# 3. Video Canvas Viewport with Technical Standby & HUD Overlay
# ============================================================================
class VideoCanvas(QLabel):
    """Hardware-styled viewport with technical standby reticle & live telemetry HUD."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(260, 146)
        self.setObjectName("videoPreview")

        self.source_name = ""
        self.stream_res = ""
        self.live_fps = 0.0
        self.is_recording = False
        self.rec_start_time = None
        self.timecode_str = "00:00:00"

        self.hud_timer = QTimer(self)
        self.hud_timer.timeout.connect(self._update_clock)
        self.hud_timer.start(250)
        self.blink_state = True

    def set_stream_info(self, name: str, width: int, height: int):
        self.source_name = name
        self.stream_res = f"{width}x{height}" if width and height else ""
        if not name:
            self.clear()
        self.update()

    def set_live_fps(self, fps: float):
        self.live_fps = fps
        self.update()

    def set_recording(self, recording: bool):
        self.is_recording = recording
        if recording:
            self.rec_start_time = time.time()
            self.timecode_str = "00:00:00"
        else:
            self.rec_start_time = None
        self.update()

    def _update_clock(self):
        self.blink_state = not self.blink_state
        if self.is_recording and self.rec_start_time:
            elapsed = int(time.time() - self.rec_start_time)
            self.timecode_str = str(timedelta(seconds=elapsed)).zfill(8)
            self.update()

    def _draw_standby_reticle(self, painter: QPainter):
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(8, 10, 13))

        # Subtle technical grid lines
        grid_pen = QPen(QColor(18, 22, 29), 1, Qt.SolidLine)
        painter.setPen(grid_pen)
        step = 32
        for x in range(step, w, step):
            painter.drawLine(x, 0, x, h)
        for y in range(step, h, step):
            painter.drawLine(0, y, w, y)

        # Center Crosshair
        cx, cy = w // 2, h // 2
        painter.setPen(QPen(QColor(35, 42, 54), 1))
        painter.drawLine(cx - 20, cy, cx + 20, cy)
        painter.drawLine(cx, cy - 20, cx, cy + 20)

        # Corner framing brackets
        b_len = 14
        m = 14
        bracket_pen = QPen(QColor(48, 58, 74), 2)
        painter.setPen(bracket_pen)
        # Top-Left
        painter.drawLine(m, m, m + b_len, m)
        painter.drawLine(m, m, m, m + b_len)
        # Top-Right
        painter.drawLine(w - m, m, w - m - b_len, m)
        painter.drawLine(w - m, m, w - m, m + b_len)
        # Bottom-Left
        painter.drawLine(m, h - m, m + b_len, h - m)
        painter.drawLine(m, h - m, m, h - m - b_len)
        # Bottom-Right
        painter.drawLine(w - m, h - m, w - m - b_len, h - m)
        painter.drawLine(w - m, h - m, w - m, h - m - b_len)

        # Standby Telemetry Text
        font = QFont("Menlo", 9, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(75, 86, 105))
        painter.drawText(
            QRect(0, 0, w, h), Qt.AlignCenter, "NO NDI SIGNAL // STANDBY"
        )

    def paintEvent(self, event):
        if not self.source_name:
            painter = QPainter(self)
            self._draw_standby_reticle(painter)
            return

        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        font = QFont("Menlo", 8, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)

        pad_x = 8
        pad_y = 4
        margin = 8
        bg_color = QColor(10, 12, 16, 210)
        border_color = QColor(45, 55, 72, 180)

        # 1. Top-Left: Source & Resolution Telemetry Pill
        header_text = self.source_name.upper()
        if self.stream_res:
            header_text += f"  |  {self.stream_res}"
        text_w = fm.horizontalAdvance(header_text)
        text_h = fm.height()

        box_tl = QRect(margin, margin, text_w + pad_x * 2, text_h + pad_y * 2)
        painter.setPen(QPen(border_color, 1))
        painter.setBrush(bg_color)
        painter.drawRoundedRect(box_tl, 3, 3)

        painter.setPen(QColor(225, 232, 240))
        painter.drawText(box_tl, Qt.AlignCenter, header_text)

        # 2. Top-Right: Live FPS Telemetry Pill
        if self.live_fps > 0:
            fps_text = f"{self.live_fps:.1f} FPS"
            fps_w = fm.horizontalAdvance(fps_text)
            box_tr = QRect(
                self.width() - margin - fps_w - pad_x * 2,
                margin,
                fps_w + pad_x * 2,
                text_h + pad_y * 2,
            )
            painter.setPen(QPen(border_color, 1))
            painter.setBrush(bg_color)
            painter.drawRoundedRect(box_tr, 3, 3)

            fps_color = (
                QColor(0, 230, 118)
                if self.live_fps >= 28.0
                else QColor(255, 180, 0)
            )
            painter.setPen(fps_color)
            painter.drawText(box_tr, Qt.AlignCenter, fps_text)

        # 3. Bottom-Left: ISO Recording Timecode Badge
        if self.is_recording:
            rec_text = f"REC  {self.timecode_str}"
            rec_w = fm.horizontalAdvance(rec_text) + 16
            box_bl = QRect(
                margin,
                self.height() - margin - text_h - pad_y * 2,
                rec_w + pad_x * 2,
                text_h + pad_y * 2,
            )

            painter.setPen(QPen(QColor(229, 20, 0, 220), 1))
            painter.setBrush(QColor(28, 6, 6, 225))
            painter.drawRoundedRect(box_bl, 3, 3)

            if self.blink_state:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 30, 30))
                painter.drawEllipse(
                    box_bl.x() + pad_x,
                    box_bl.y() + (box_bl.height() - 8) // 2,
                    8,
                    8,
                )

            painter.setPen(QColor(255, 240, 240))
            text_rect = QRect(
                box_bl.x() + pad_x + 14,
                box_bl.y(),
                box_bl.width() - pad_x - 14,
                box_bl.height(),
            )
            painter.drawText(
                text_rect, Qt.AlignVCenter | Qt.AlignLeft, rec_text
            )


# ============================================================================
# 4. NDI Background Ingestion & FFmpeg Worker Thread
# ============================================================================
class NDIStreamWorker(QThread):
    frame_ready = Signal(QImage)
    audio_levels = Signal(float, float)
    stream_specs = Signal(int, int, float)

    def __init__(self, source_name, selected_encoder="libx264"):
        super().__init__()
        self.source_name = source_name
        self.encoder = selected_encoder
        self.running = True
        self.recording = False
        self.record_process = None

        self.v_server = None
        self.a_server = None
        self.v_conn = None
        self.a_conn = None

        self.frame_count = 0
        self.last_fps_calc = time.time()
        self.current_fps = 0.0
        self.last_audio_emit = 0.0

    def _create_server_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s, s.getsockname()[1]

    def start_recording(
        self,
        output_filename,
        width,
        height,
        fps=30,
        sample_rate=48000,
        channels=2,
    ):
        self.v_server, v_port = self._create_server_socket()
        self.a_server, a_port = self._create_server_socket()

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "bgr0",
            "-s",
            f"{width}x{height}",
            "-r",
            str(int(fps or 30)),
            "-i",
            f"tcp://127.0.0.1:{v_port}",
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-i",
            f"tcp://127.0.0.1:{a_port}",
            "-c:v",
            self.encoder,
        ]

        if self.encoder == "h264_nvenc":
            cmd += ["-preset", "p4", "-tune", "ll", "-cq", "20"]
        elif self.encoder == "h264_qsv":
            cmd += ["-preset", "veryfast", "-global_quality", "20"]
        elif self.encoder == "h264_videotoolbox":
            cmd += ["-realtime", "true", "-q:v", "65"]
        else:
            cmd += ["-preset", "veryfast", "-crf", "20"]

        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest", output_filename]

        self.record_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.v_conn, _ = self.v_server.accept()
        self.a_conn, _ = self.a_server.accept()
        self.recording = True

    def stop_recording(self):
        self.recording = False
        for conn in [self.v_conn, self.a_conn, self.v_server, self.a_server]:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        self.v_conn, self.a_conn, self.v_server, self.a_server = (
            None,
            None,
            None,
            None,
        )

        if self.record_process:
            try:
                self.record_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.record_process.kill()
            self.record_process = None

    def _calculate_channel_level(self, samples: np.ndarray) -> float:
        if len(samples) == 0:
            return 0.0
        rms = np.sqrt(np.mean(samples**2))
        if rms < 1e-5:
            return 0.0
        db = 20.0 * np.log10(rms)
        norm = (db + 60.0) / 60.0
        return float(np.clip(norm, 0.0, 1.0))

    def run(self):
        recv_create = ndi.RecvCreateV3()
        recv_create.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
        recv_create.bandwidth = ndi.RECV_BANDWIDTH_HIGHEST
        recv = ndi.recv_create_v3(recv_create)

        source = ndi.Source()
        source.p_ndi_name = self.source_name
        ndi.recv_connect(recv, source)

        while self.running:
            type_, video_frame, audio_frame, _ = ndi.recv_capture_v2(recv, 50)

            if type_ == ndi.FRAME_TYPE_VIDEO:
                width, height = video_frame.xres, video_frame.yres
                data = np.copy(video_frame.data)

                self.frame_count += 1
                now = time.time()
                elapsed = now - self.last_fps_calc
                if elapsed >= 1.0:
                    self.current_fps = self.frame_count / elapsed
                    self.frame_count = 0
                    self.last_fps_calc = now
                    self.stream_specs.emit(width, height, self.current_fps)

                if self.recording and self.v_conn:
                    try:
                        self.v_conn.sendall(data.tobytes())
                    except (BrokenPipeError, ConnectionResetError):
                        self.stop_recording()

                qimg = QImage(
                    data.data,
                    width,
                    height,
                    video_frame.line_stride_in_bytes,
                    QImage.Format_RGB32,
                )
                self.frame_ready.emit(qimg.copy())
                ndi.recv_free_video_v2(recv, video_frame)

            elif type_ == ndi.FRAME_TYPE_AUDIO:
                audio_data = np.copy(audio_frame.data)
                channels = audio_frame.no_channels

                if self.recording and self.a_conn:
                    interleaved = audio_data.T.astype(np.float32).tobytes()
                    try:
                        self.a_conn.sendall(interleaved)
                    except (BrokenPipeError, ConnectionResetError):
                        self.stop_recording()

                now = time.time()
                if now - self.last_audio_emit > 0.033:
                    self.last_audio_emit = now
                    left_norm = (
                        self._calculate_channel_level(audio_data[0])
                        if channels >= 1
                        else 0.0
                    )
                    right_norm = (
                        self._calculate_channel_level(audio_data[1])
                        if channels >= 2
                        else left_norm
                    )
                    self.audio_levels.emit(left_norm, right_norm)

                ndi.recv_free_audio_v2(recv, audio_frame)

        self.stop_recording()
        ndi.recv_destroy(recv)

    def stop(self):
        self.running = False
        self.wait()


# ============================================================================
# 5. Industrial Rack Module Video Tile Component
# ============================================================================
class VideoTile(QFrame):
    tile_focused = Signal(int)

    def __init__(self, tile_id, detected_encoder="libx264"):
        super().__init__()
        self.tile_id = tile_id
        self.detected_encoder = detected_encoder
        self.worker = None

        self.is_focused = False
        self.is_recording = False

        self.setObjectName("videoTileFrame")
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(6, 6, 6, 6)
        self.main_layout.setSpacing(6)

        # --- Module Header Bar ---
        header_bar = QHBoxLayout()
        header_bar.setContentsMargins(2, 0, 2, 0)

        self.cam_label = QLabel(f"CAM {tile_id + 1:02d}")
        self.cam_label.setStyleSheet(
            "color: #E0E6ED; font-family: 'Menlo', 'Consolas', monospace; "
            "font-size: 11px; font-weight: bold; letter-spacing: 1px;"
        )
        header_bar.addWidget(self.cam_label)

        header_bar.addStretch()

        self.tally_badge = QLabel("STANDBY")
        self.tally_badge.setAlignment(Qt.AlignCenter)
        header_bar.addWidget(self.tally_badge)

        self.main_layout.addLayout(header_bar)

        # --- Viewport + Stereo LED VU Meter ---
        view_layout = QHBoxLayout()
        view_layout.setSpacing(5)
        self.preview_label = VideoCanvas()
        view_layout.addWidget(self.preview_label, stretch=1)

        self.vu_meter = VUMeterWidget()
        view_layout.addWidget(self.vu_meter, stretch=0)
        self.main_layout.addLayout(view_layout, stretch=1)

        # --- Bottom Hardware Control Strip ---
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(6)
        self.source_selector = QComboBox()
        self.source_selector.addItem("--- ASSIGN NDI INPUT ---")
        ctrl_bar.addWidget(self.source_selector, stretch=1)

        self.btn_record = QPushButton("● ISO REC")
        self.btn_record.setEnabled(False)
        self.btn_record.setCheckable(True)
        self.btn_record.setFixedWidth(95)
        self.btn_record.clicked.connect(self.toggle_recording)
        ctrl_bar.addWidget(self.btn_record)

        self.main_layout.addLayout(ctrl_bar)
        self.source_selector.activated.connect(self.on_source_selected)
        self.update_tally_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tile_focused.emit(self.tile_id)
        super().mousePressEvent(event)

    def set_focus_state(self, focused: bool):
        self.is_focused = focused
        self.update_tally_style()

    def update_tally_style(self):
        if self.is_recording:
            border_color = "#E51400"  # Program Red
            tile_bg = "#1A0F10"
            badge_text = "PGM REC"
            badge_style = (
                "background-color: #E51400; color: #FFFFFF; border-radius: 2px; "
                "padding: 2px 6px; font-family: 'Menlo', monospace; font-size: 9px; font-weight: bold;"
            )
        elif self.is_focused:
            border_color = "#00E676"  # Preview Green
            tile_bg = "#101915"
            badge_text = "PVW SEL"
            badge_style = (
                "background-color: #00C853; color: #050709; border-radius: 2px; "
                "padding: 2px 6px; font-family: 'Menlo', monospace; font-size: 9px; font-weight: bold;"
            )
        else:
            border_color = "#242A34"  # Neutral Chassis Gray
            tile_bg = "#13161C"
            badge_text = "ONLINE" if self.is_assigned() else "STANDBY"
            badge_style = (
                "background-color: #1F242D; color: #768396; border-radius: 2px; "
                "padding: 2px 6px; font-family: 'Menlo', monospace; font-size: 9px; font-weight: bold;"
            )

        self.tally_badge.setText(badge_text)
        self.tally_badge.setStyleSheet(badge_style)

        self.setStyleSheet(f"""
            QFrame#videoTileFrame {{
                background-color: {tile_bg};
                border: 2px solid {border_color};
                border-radius: 5px;
            }}
        """)

        self.preview_label.setStyleSheet(f"""
            QLabel#videoPreview {{
                background-color: #080A0D;
                border: 1px solid #1E242E;
                border-radius: 3px;
            }}
        """)

    def is_assigned(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def update_source_list(self, sources):
        current_selection = self.source_selector.currentText()
        self.source_selector.blockSignals(True)
        self.source_selector.clear()
        self.source_selector.addItem("--- ASSIGN NDI INPUT ---")
        for src in sources:
            self.source_selector.addItem(src)

        idx = self.source_selector.findText(current_selection)
        if idx >= 0:
            self.source_selector.setCurrentIndex(idx)
        self.source_selector.blockSignals(False)

    def on_source_selected(self):
        selected_text = self.source_selector.currentText()
        if self.worker:
            self.worker.stop()
            self.worker = None
            self.btn_record.setEnabled(False)
            self.btn_record.setChecked(False)
            self.is_recording = False
            self.vu_meter.update_levels(0.0, 0.0)
            self.preview_label.set_stream_info("", 0, 0)
            self.preview_label.set_recording(False)

        if selected_text != "--- ASSIGN NDI INPUT ---":
            clean_display_name = (
                selected_text.split(" ")[0]
                if "(" in selected_text
                else selected_text
            )
            self.preview_label.set_stream_info(clean_display_name, 0, 0)

            self.worker = NDIStreamWorker(
                selected_text, selected_encoder=self.detected_encoder
            )
            self.worker.frame_ready.connect(self.render_frame)
            self.worker.audio_levels.connect(self.vu_meter.update_levels)
            self.worker.stream_specs.connect(self.on_specs_updated)
            self.worker.start()
            self.btn_record.setEnabled(True)

        self.update_tally_style()

    @Slot(int, int, float)
    def on_specs_updated(self, w: int, h: int, fps: float):
        clean_name = self.source_selector.currentText().split(" ")[0]
        self.preview_label.set_stream_info(clean_name, w, h)
        self.preview_label.set_live_fps(fps)

    @Slot(QImage)
    def render_frame(self, img):
        scaled = img.scaled(
            self.preview_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.preview_label.setPixmap(QPixmap.fromImage(scaled))

    def start_tile_recording(self, output_dir="captures", session_tag=None):
        if not self.is_assigned() or self.is_recording:
            return False

        os.makedirs(output_dir, exist_ok=True)
        timestamp = session_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_src = "".join(
            c
            for c in self.source_selector.currentText()
            if c.isalnum() or c in ("-", "_")
        ).strip()
        filename = (
            f"{timestamp}_CAM{self.tile_id + 1:02d}_{safe_src or 'Stream'}.mp4"
        )
        filepath = os.path.join(output_dir, filename)

        self.worker.start_recording(filepath, 1920, 1080)
        self.is_recording = True
        self.preview_label.set_recording(True)

        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(True)
        self.btn_record.setText("■ STOP ISO")
        self.btn_record.setStyleSheet(
            "background-color: #E51400; color: #FFFFFF; border: 1px solid #FF5555; font-weight: bold;"
        )
        self.btn_record.blockSignals(False)

        self.update_tally_style()
        return True

    def stop_tile_recording(self):
        if not self.is_recording:
            return
        if self.worker:
            self.worker.stop_recording()

        self.is_recording = False
        self.preview_label.set_recording(False)

        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(False)
        self.btn_record.setText("● ISO REC")
        self.btn_record.setStyleSheet("")
        self.btn_record.blockSignals(False)

        self.update_tally_style()

    def toggle_recording(self, checked):
        if checked:
            self.start_tile_recording()
        else:
            self.stop_tile_recording()

    def cleanup(self):
        if self.worker:
            self.worker.stop()


# ============================================================================
# 6. Application Main Window & Multi-Camera Controller
# ============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NDI MULTIVIEWER // ISO RECORDING SYSTEM")
        self.resize(1360, 820)
        self.setStyleSheet(INDUSTRIAL_STYLESHEET)

        self.detected_encoder = detect_best_h264_encoder()
        self.statusBar().showMessage(
            f"SYSTEM READY  //  ACTIVE HW ENCODER: {self.detected_encoder.upper()}  //  NDI CORE INITIALIZED"
        )

        if not ndi.initialize():
            raise SystemError("Failed to initialize NDI SDK.")
        self.find_instance = ndi.find_create_v2()

        self.tiles = []
        self.focused_tile_id = None
        self.master_recording = False

        self._setup_toolbar()

        central = QWidget()
        self.setCentralWidget(central)
        self.main_layout = QVBoxLayout(central)
        self.main_layout.setContentsMargins(8, 8, 8, 8)

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(8)
        self.main_layout.addWidget(self.grid_container, stretch=1)

        self.rebuild_grid()

    def _setup_toolbar(self):
        toolbar = QToolBar("Broadcast Control Bar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # System Title Branding Badge
        brand_lbl = QLabel("NDI // MULTIVIEWER")
        brand_lbl.setStyleSheet(
            "color: #00E5FF; font-family: 'Menlo', monospace; font-size: 12px; "
            "font-weight: bold; padding-right: 12px;"
        )
        toolbar.addWidget(brand_lbl)
        toolbar.addSeparator()

        toolbar.addWidget(QLabel("  MATRIX LAYOUT:"))
        self.row_spin = QSpinBox()
        self.row_spin.setRange(1, 4)
        self.row_spin.setValue(2)
        toolbar.addWidget(self.row_spin)

        toolbar.addWidget(QLabel("X"))
        self.col_spin = QSpinBox()
        self.col_spin.setRange(1, 4)
        self.col_spin.setValue(2)
        toolbar.addWidget(self.col_spin)

        btn_apply_grid = QPushButton("REBUILD MATRIX")
        btn_apply_grid.clicked.connect(self.rebuild_grid)
        toolbar.addWidget(btn_apply_grid)

        toolbar.addSeparator()

        btn_refresh = QPushButton("⟳ SCAN NDI SUBNET")
        btn_refresh.clicked.connect(self.poll_ndi_sources)
        toolbar.addWidget(btn_refresh)

        # Spacer to push Master Record button to the right
        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().Policy.Expanding,
            spacer.sizePolicy().Policy.Preferred,
        )
        toolbar.addWidget(spacer)

        self.btn_master_rec = QPushButton("● MASTER ISO RECORD ALL")
        self.btn_master_rec.setCheckable(True)
        self.btn_master_rec.setStyleSheet("""
            QPushButton {
                background-color: #241214;
                color: #FF5252;
                font-weight: bold;
                padding: 7px 16px;
                border: 1px solid #FF5252;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #38191C;
                color: #FFFFFF;
            }
        """)
        self.btn_master_rec.clicked.connect(self.toggle_master_record)
        toolbar.addWidget(self.btn_master_rec)

    def toggle_master_record(self, checked):
        active_tiles = [t for t in self.tiles if t.is_assigned()]
        if not active_tiles:
            self.statusBar().showMessage(
                "WARNING // NO ACTIVE NDI FEEDS ASSIGNED TO MATRIX FOR RECORDING",
                4000,
            )
            self.btn_master_rec.setChecked(False)
            return

        if checked:
            session_tag = f"Take_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            started_count = 0
            for tile in active_tiles:
                if tile.start_tile_recording(session_tag=session_tag):
                    started_count += 1
            self.master_recording = True
            self.btn_master_rec.setText("■ STOP MASTER ISO RECORD")
            self.btn_master_rec.setStyleSheet("""
                QPushButton {
                    background-color: #E51400;
                    color: #FFFFFF;
                    font-weight: bold;
                    padding: 7px 16px;
                    border: 1px solid #FFFFFF;
                    border-radius: 3px;
                }
            """)
            self.statusBar().showMessage(
                f"MASTER ISO ACTIVE  //  {started_count} TRACKS ARMED  //  SESSION: {session_tag}"
            )
        else:
            for tile in self.tiles:
                tile.stop_tile_recording()
            self.master_recording = False
            self.btn_master_rec.setText("● MASTER ISO RECORD ALL")
            self.btn_master_rec.setStyleSheet("""
                QPushButton {
                    background-color: #241214;
                    color: #FF5252;
                    font-weight: bold;
                    padding: 7px 16px;
                    border: 1px solid #FF5252;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #38191C;
                    color: #FFFFFF;
                }
            """)
            self.statusBar().showMessage(
                "MASTER RECORDING HALTED  //  ALL ISO FILES FINALIZED TO /captures",
                5000,
            )

    def poll_ndi_sources(self):
        ndi.find_wait_for_sources(self.find_instance, 800)
        sources = ndi.find_get_current_sources(self.find_instance)
        source_names = [s.p_ndi_name for s in sources]

        for tile in self.tiles:
            tile.update_source_list(source_names)

        self.statusBar().showMessage(
            f"SUBNET SCAN COMPLETE  //  {len(source_names)} NDI SENDER(S) ONLINE  //  ENCODER: {self.detected_encoder.upper()}",
            4000,
        )

    def rebuild_grid(self):
        for tile in self.tiles:
            tile.cleanup()
            self.grid_layout.removeWidget(tile)
            tile.deleteLater()
        self.tiles.clear()
        self.focused_tile_id = None

        rows = self.row_spin.value()
        cols = self.col_spin.value()

        tile_id = 0
        for r in range(rows):
            for c in range(cols):
                tile = VideoTile(
                    tile_id, detected_encoder=self.detected_encoder
                )
                tile.tile_focused.connect(self.handle_tile_focus)
                self.grid_layout.addWidget(tile, r, c)
                self.tiles.append(tile)
                tile_id += 1

        self.poll_ndi_sources()

    @Slot(int)
    def handle_tile_focus(self, clicked_id):
        self.focused_tile_id = clicked_id
        for tile in self.tiles:
            tile.set_focus_state(tile.tile_id == clicked_id)

    def closeEvent(self, event):
        for tile in self.tiles:
            tile.cleanup()
        ndi.find_destroy(self.find_instance)
        ndi.destroy()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
