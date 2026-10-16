import sys
import os
import time
import socket
import subprocess
import platform
from datetime import datetime, timedelta
import numpy as np
import NDIlib as ndi

from PySide6.QtCore import Qt, QThread, Signal, Slot, QRect, QTimer
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor,QLinearGradient, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, 
    QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, 
    QLabel, QSpinBox, QGroupBox, QToolBar
)


# ============================================================================
# 1. Hardware GPU Encoder Detection Utility
# ============================================================================
def detect_best_h264_encoder():
    """Detects available hardware-accelerated H.264 encoders in FFmpeg."""
    try:
        output = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"], 
            stderr=subprocess.STDOUT, 
            text=True
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

    # Verify candidate can actually initialize with system GPU drivers
    for enc in candidates:
        test_cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
            "-c:v", enc, "-f", "null", "-"
        ]
        res = subprocess.run(test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            return enc

    return "libx264"


# ============================================================================
# 2. Stereo VU Meter Widget
# ============================================================================
class VUMeterWidget(QWidget):
    """Draws a vertical stereo (L/R) audio meter with smooth decay ballistics."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(24)
        self.setMinimumHeight(120)

        self.left_level = 0.0
        self.right_level = 0.0
        self.left_peak = 0.0
        self.right_peak = 0.0

        # UI decay refresh timer (~30 FPS)
        self.decay_timer = QTimer(self)
        self.decay_timer.timeout.connect(self._decay)
        self.decay_timer.start(33)

    @Slot(float, float)
    def update_levels(self, left: float, right: float):
        """Accepts normalized values between 0.0 and 1.0."""
        self.left_level = max(self.left_level, left)
        self.right_level = max(self.right_level, right)
        self.left_peak = max(self.left_peak, left)
        self.right_peak = max(self.right_peak, right)
        self.update()

    def _decay(self):
        decay_factor = 0.92
        self.left_level *= decay_factor
        self.right_level *= decay_factor
        self.left_peak = max(self.left_level, self.left_peak * 0.98)
        self.right_peak = max(self.right_level, self.right_peak * 0.98)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        w, h = self.width(), self.height()
        bar_w = (w - 6) // 2

        # Background
        painter.fillRect(0, 0, w, h, QColor(20, 20, 20))

        # Vertical Gradient Thresholds
        gradient = QLinearGradient(0, h, 0, 0)
        gradient.setColorAt(0.0, QColor(40, 180, 40))   # Low (-60dB)
        gradient.setColorAt(0.70, QColor(180, 200, 30)) # Nominal (~ -18dB)
        gradient.setColorAt(0.90, QColor(230, 140, 20)) # Warning (~ -6dB)
        gradient.setColorAt(1.0, QColor(230, 30, 30))   # Clip (0dB)

        channels = [(self.left_level, self.left_peak), (self.right_level, self.right_peak)]
        for i, (level, peak) in enumerate(channels):
            x = 2 + i * (bar_w + 2)
            bar_height = int(level * h)
            y = h - bar_height

            # Active level bar
            painter.fillRect(x, y, bar_w, bar_height, gradient)

            # Peak-hold tick
            if peak > 0.02:
                peak_y = max(0, h - int(peak * h) - 2)
                painter.fillRect(x, peak_y, bar_w, 2, QColor(255, 255, 255))


# ============================================================================
# 3. Video Canvas Viewport with OSD / HUD Overlay
# ============================================================================
class VideoCanvas(QLabel):
    """Video viewport that paints HUD badges, timecode, and stream metrics."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(240, 135)
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

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.source_name:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        font = QFont("Helvetica", 9, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)

        pad = 6
        margin = 8
        bg_brush = QColor(0, 0, 0, 160)

        # Top-Left: Source Name & Resolution Badge
        header_text = self.source_name
        if self.stream_res:
            header_text += f" [{self.stream_res}]"
        text_w = fm.horizontalAdvance(header_text)
        text_h = fm.height()

        box_tl = QRect(margin, margin, text_w + pad * 2, text_h + pad)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg_brush)
        painter.drawRoundedRect(box_tl, 4, 4)
        painter.setPen(QColor(240, 240, 240))
        painter.drawText(box_tl, Qt.AlignCenter, header_text)

        # Top-Right: Input FPS Badge
        if self.live_fps > 0:
            fps_text = f"{self.live_fps:.1f} fps"
            fps_w = fm.horizontalAdvance(fps_text)
            box_tr = QRect(self.width() - margin - fps_w - pad * 2, margin, fps_w + pad * 2, text_h + pad)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg_brush)
            painter.drawRoundedRect(box_tr, 4, 4)

            fps_color = QColor(0, 220, 80) if self.live_fps >= 28.0 else QColor(255, 170, 0)
            painter.setPen(fps_color)
            painter.drawText(box_tr, Qt.AlignCenter, fps_text)

        # Bottom-Left: Recording Status & Timecode
        if self.is_recording:
            rec_text = f"REC {self.timecode_str}"
            rec_w = fm.horizontalAdvance(rec_text) + 14
            box_bl = QRect(margin, self.height() - margin - text_h - pad, rec_w + pad * 2, text_h + pad)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(20, 0, 0, 200))
            painter.drawRoundedRect(box_bl, 4, 4)

            if self.blink_state:
                painter.setBrush(QColor(230, 20, 20))
                painter.drawEllipse(box_bl.x() + pad, box_bl.y() + (box_bl.height() - 8) // 2, 8, 8)

            painter.setPen(QColor(255, 255, 255))
            text_rect = QRect(box_bl.x() + pad + 12, box_bl.y(), box_bl.width() - pad - 12, box_bl.height())
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, rec_text)


# ============================================================================
# 4. NDI Background Ingestion & FFmpeg Worker Thread
# ============================================================================
class NDIStreamWorker(QThread):
    frame_ready = Signal(QImage)
    audio_levels = Signal(float, float)
    stream_specs = Signal(int, int, float)  # (width, height, fps)

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
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        return s, s.getsockname()[1]

    def start_recording(self, output_filename, width, height, fps=30, sample_rate=48000, channels=2):
        self.v_server, v_port = self._create_server_socket()
        self.a_server, a_port = self._create_server_socket()

        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "bgr0",
            "-s", f"{width}x{height}", "-r", str(int(fps or 30)),
            "-i", f"tcp://127.0.0.1:{v_port}",
            "-f", "f32le", "-ar", str(sample_rate), "-ac", str(channels),
            "-i", f"tcp://127.0.0.1:{a_port}",
            "-c:v", self.encoder
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

        self.record_process = subprocess.Popen(cmd)
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
        self.v_conn, self.a_conn, self.v_server, self.a_server = None, None, None, None

        if self.record_process:
            try:
                self.record_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.record_process.kill()
            self.record_process = None

    def _calculate_channel_level(self, samples: np.ndarray) -> float:
        if len(samples) == 0:
            return 0.0
        rms = np.sqrt(np.mean(samples ** 2))
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

                # Framerate measurement window
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
                    data.data, width, height, video_frame.line_stride_in_bytes, QImage.Format_RGB32
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
                    left_norm = self._calculate_channel_level(audio_data[0]) if channels >= 1 else 0.0
                    right_norm = self._calculate_channel_level(audio_data[1]) if channels >= 2 else left_norm
                    self.audio_levels.emit(left_norm, right_norm)

                ndi.recv_free_audio_v2(recv, audio_frame)

        self.stop_recording()
        ndi.recv_destroy(recv)

    def stop(self):
        self.running = False
        self.wait()


# ============================================================================
# 5. Video Tile UI Component with Tally Control
# ============================================================================
class VideoTile(QGroupBox):
    tile_focused = Signal(int)

    def __init__(self, tile_id, detected_encoder="libx264"):
        super().__init__(f"Tile {tile_id + 1}")
        self.tile_id = tile_id
        self.detected_encoder = detected_encoder
        self.worker = None

        self.is_focused = False
        self.is_recording = False

        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        view_layout = QHBoxLayout()
        self.preview_label = VideoCanvas()
        view_layout.addWidget(self.preview_label, stretch=1)

        self.vu_meter = VUMeterWidget()
        view_layout.addWidget(self.vu_meter, stretch=0)
        self.main_layout.addLayout(view_layout, stretch=1)

        ctrl_bar = QHBoxLayout()
        self.source_selector = QComboBox()
        self.source_selector.addItem("--- Select Source ---")
        ctrl_bar.addWidget(self.source_selector)

        self.btn_record = QPushButton("Record")
        self.btn_record.setEnabled(False)
        self.btn_record.setCheckable(True)
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
        elif self.is_focused:
            border_color = "#00CC44"  # Preview Green
        else:
            border_color = "#2E2E2E"  # Idle Dark Slate

        self.preview_label.setStyleSheet(f"""
            QLabel#videoPreview {{
                background-color: #121212;
                color: #777;
                border: 3px solid {border_color};
                border-radius: 4px;
            }}
        """)

    def is_assigned(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def update_source_list(self, sources):
        current_selection = self.source_selector.currentText()
        self.source_selector.blockSignals(True)
        self.source_selector.clear()
        self.source_selector.addItem("--- Select Source ---")
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
            self.update_tally_style()

        if selected_text != "--- Select Source ---":
            clean_display_name = selected_text.split(" ")[0] if "(" in selected_text else selected_text
            self.preview_label.set_stream_info(clean_display_name, 0, 0)

            self.worker = NDIStreamWorker(selected_text, selected_encoder=self.detected_encoder)
            self.worker.frame_ready.connect(self.render_frame)
            self.worker.audio_levels.connect(self.vu_meter.update_levels)
            self.worker.stream_specs.connect(self.on_specs_updated)
            self.worker.start()
            self.btn_record.setEnabled(True)

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
            Qt.SmoothTransformation
        )
        self.preview_label.setPixmap(QPixmap.fromImage(scaled))

    def start_tile_recording(self, output_dir="captures", session_tag=None):
        if not self.is_assigned() or self.is_recording:
            return False

        os.makedirs(output_dir, exist_ok=True)
        timestamp = session_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_src = "".join(c for c in self.source_selector.currentText() if c.isalnum() or c in ("-", "_")).strip()
        filename = f"{timestamp}_Tile{self.tile_id + 1}_{safe_src or 'Stream'}.mp4"
        filepath = os.path.join(output_dir, filename)

        self.worker.start_recording(filepath, 1920, 1080)
        self.is_recording = True
        self.preview_label.set_recording(True)

        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(True)
        self.btn_record.setText("Stop Rec")
        self.btn_record.setStyleSheet("background-color: #E51400; color: white; font-weight: bold;")
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
        self.btn_record.setText("Record")
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
        self.setWindowTitle("NDI MultiView & ISO Recorder")
        self.resize(1280, 720)

        # Hardware Encoder Probe
        self.detected_encoder = detect_best_h264_encoder()
        self.statusBar().showMessage(f"Active Hardware Video Encoder: {self.detected_encoder}")

        # NDI Initialization
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

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.main_layout.addWidget(self.grid_container, stretch=1)

        self.rebuild_grid()

    def _setup_toolbar(self):
        toolbar = QToolBar("Main Controls")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel(" Grid: "))
        self.row_spin = QSpinBox()
        self.row_spin.setRange(1, 4)
        self.row_spin.setValue(2)
        toolbar.addWidget(self.row_spin)

        toolbar.addWidget(QLabel(" x "))
        self.col_spin = QSpinBox()
        self.col_spin.setRange(1, 4)
        self.col_spin.setValue(2)
        toolbar.addWidget(self.col_spin)

        btn_apply_grid = QPushButton("Apply Grid")
        btn_apply_grid.clicked.connect(self.rebuild_grid)
        toolbar.addWidget(btn_apply_grid)

        toolbar.addSeparator()

        btn_refresh = QPushButton("Discover Sources")
        btn_refresh.clicked.connect(self.poll_ndi_sources)
        toolbar.addWidget(btn_refresh)

        toolbar.addSeparator()

        self.btn_master_rec = QPushButton("🔴 Master Record ALL")
        self.btn_master_rec.setCheckable(True)
        self.btn_master_rec.setStyleSheet("""
            QPushButton {
                background-color: #2b2b2b;
                color: #ff5555;
                font-weight: bold;
                padding: 6px 14px;
                border: 1px solid #ff5555;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #3b2020;
            }
        """)
        self.btn_master_rec.clicked.connect(self.toggle_master_record)
        toolbar.addWidget(self.btn_master_rec)

    def toggle_master_record(self, checked):
        active_tiles = [t for t in self.tiles if t.is_assigned()]
        if not active_tiles:
            self.statusBar().showMessage("No active NDI feeds available to record.", 3000)
            self.btn_master_rec.setChecked(False)
            return

        if checked:
            session_tag = f"Take_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            started_count = 0
            for tile in active_tiles:
                if tile.start_tile_recording(session_tag=session_tag):
                    started_count += 1
            self.master_recording = True
            self.btn_master_rec.setText("⏹ Stop Master Record")
            self.btn_master_rec.setStyleSheet("""
                QPushButton {
                    background-color: #E51400;
                    color: white;
                    font-weight: bold;
                    padding: 6px 14px;
                    border: 1px solid #ffffff;
                    border-radius: 4px;
                }
            """)
            self.statusBar().showMessage(f"Master Recording: {started_count} ISO tracks armed ({session_tag})")
        else:
            for tile in self.tiles:
                tile.stop_tile_recording()
            self.master_recording = False
            self.btn_master_rec.setText("🔴 Master Record ALL")
            self.btn_master_rec.setStyleSheet("""
                QPushButton {
                    background-color: #2b2b2b;
                    color: #ff5555;
                    font-weight: bold;
                    padding: 6px 14px;
                    border: 1px solid #ff5555;
                    border-radius: 4px;
                }
            """)
            self.statusBar().showMessage("Master Recording stopped. All files finalized.", 4000)

    def poll_ndi_sources(self):
        """Scans the local subnet for active NDI sources and updates selectors."""
        ndi.find_wait_for_sources(self.find_instance, 1000)
        sources = ndi.find_get_current_sources(self.find_instance)
        source_names = [s.p_ndi_name for s in sources]

        for tile in self.tiles:
            tile.update_source_list(source_names)

    def rebuild_grid(self):
        """Clears existing tile widgets and builds a clean layout grid."""
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
                tile = VideoTile(tile_id, detected_encoder=self.detected_encoder)
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


# ============================================================================
# 7. Application Entry Point
# ============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
