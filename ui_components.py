import os
import time
from datetime import datetime, timedelta
from PySide6.QtCore import Qt, QRect, QTimer, Signal, Slot
from PySide6.QtGui import (
    QPainter, QColor, QLinearGradient, QFont, QFontMetrics,
    QImage, QPixmap, QPen
)
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QPushButton, QLineEdit, QLabel, QSizePolicy,
    QDialog, QFileDialog, QDialogButtonBox, QCheckBox
)

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
    HAS_QT_MULTIMEDIA = True
except ImportError:
    HAS_QT_MULTIMEDIA = False

from ndi_worker import NDIStreamWorker


DEFAULT_RECORD_DIR = os.path.abspath("captures")


def create_carbon_fiber_pixmap() -> QPixmap:
    """Generates a seamless 16x16 diagonal carbon-fiber weave texture tile."""
    size = 16
    half = 8
    pix = QPixmap(size, size)
    pix.fill(QColor(15, 15, 15))

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, False)

    # Draw 2x2 diagonal twill weave cells
    for row in range(2):
        for col in range(2):
            x = col * half
            y = row * half

            if (row + col) % 2 == 0:
                # Primary weave cell (vertical-angled highlight)
                grad = QLinearGradient(x, y, x + half, y)
                grad.setColorAt(0.0, QColor(22, 22, 24))
                grad.setColorAt(0.5, QColor(44, 44, 48))
                grad.setColorAt(1.0, QColor(18, 18, 20))
            else:
                # Secondary weave cell (horizontal-angled highlight)
                grad = QLinearGradient(x, y, x, y + half)
                grad.setColorAt(0.0, QColor(18, 18, 20))
                grad.setColorAt(0.5, QColor(34, 34, 38))
                grad.setColorAt(1.0, QColor(14, 14, 16))

            painter.fillRect(x, y, half - 1, half - 1, grad)
            # Dark carbon seam border
            painter.setPen(QColor(8, 8, 10))
            painter.drawLine(x, y + half - 1, x + half - 1, y + half - 1)
            painter.drawLine(x + half - 1, y, x + half - 1, y + half - 1)

    painter.end()
    return pix


class HostAudioOutput:
    """Manages host speaker playback for a tile's NDI audio stream."""
    def __init__(self):
        self.sink = None
        self.io_device = None
        self.current_rate = 0
        self.current_channels = 0

    def write_pcm(self, pcm_bytes: bytes, sample_rate: int, channels: int):
        if not HAS_QT_MULTIMEDIA:
            return

        if (
            self.sink is None
            or self.current_rate != sample_rate
            or self.current_channels != channels
        ):
            self.stop()
            fmt = QAudioFormat()
            fmt.setSampleRate(sample_rate)
            fmt.setChannelCount(channels)
            fmt.setSampleFormat(QAudioFormat.Float)

            device = QMediaDevices.defaultAudioOutput()
            if device.isNull():
                return

            self.sink = QAudioSink(device, fmt)
            self.sink.setBufferSize(sample_rate * channels * 4 // 2)  # 0.5s buffer
            self.io_device = self.sink.start()
            self.current_rate = sample_rate
            self.current_channels = channels

        if self.io_device and self.sink:
            if self.sink.bytesFree() >= len(pcm_bytes):
                self.io_device.write(pcm_bytes)

    def stop(self):
        if self.sink:
            try:
                self.sink.stop()
            except Exception:
                pass
        self.sink = None
        self.io_device = None
        self.current_rate = 0
        self.current_channels = 0


class SlotSettingsDialog(QDialog):
    """
    Slot Settings window supporting both single-tab and multi-tab batch editing.
    """
    def __init__(self, target_tiles: list, parent=None):
        super().__init__(parent)
        self.target_tiles = target_tiles
        self.multi_mode = len(target_tiles) > 1
        first_cfg = target_tiles[0].get_config()

        if self.multi_mode:
            self.setWindowTitle(f"Slot Settings ({len(target_tiles)} Tabs Selected)")
        else:
            self.setWindowTitle(f"Slot Settings - {first_cfg['title']}")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

        if self.multi_mode:
            banner = QLabel(
                f"Editing {len(target_tiles)} selected tabs: "
                + ", ".join(t.get_tile_name() for t in target_tiles)
            )
            banner.setWordWrap(True)
            banner.setStyleSheet(
                "background-color: #2c2810; color: #ffd700; padding: 6px; "
                "border: 1px solid #ffd700; border-radius: 4px; font-weight: bold;"
            )
            layout.addWidget(banner)

        # 2a. Tab Title
        self.title_edit = QLineEdit()
        if self.multi_mode:
            self.title_edit.setPlaceholderText("Leave blank to keep individual tab titles...")
        else:
            self.title_edit.setText(first_cfg.get("title", ""))
        form.addRow("Tab Title:", self.title_edit)

        # 2b. Record Location (Default vs Custom)
        rec_box = QVBoxLayout()
        self.use_custom_dir_cb = QCheckBox("Use custom record location (other than default ./captures)")
        is_custom_dir = first_cfg.get("use_custom_dir", False)
        self.use_custom_dir_cb.setChecked(is_custom_dir)
        rec_box.addWidget(self.use_custom_dir_cb)

        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(first_cfg.get("record_dir", DEFAULT_RECORD_DIR))
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.dir_edit, stretch=1)
        dir_row.addWidget(self.btn_browse)
        rec_box.addLayout(dir_row)

        self.dir_edit.setEnabled(is_custom_dir)
        self.btn_browse.setEnabled(is_custom_dir)
        self.use_custom_dir_cb.toggled.connect(self._on_custom_dir_toggled)
        form.addRow("Record Location:", rec_box)

        # 2c. Text Overlay Mode
        self.overlay_mode_combo = QComboBox()
        self.overlay_mode_combo.addItem("Not Shown", "none")
        self.overlay_mode_combo.addItem("Use Tab Name", "tab_name")
        self.overlay_mode_combo.addItem("Manually Entered Text", "custom")

        current_mode = first_cfg.get("overlay_mode", "tab_name")
        idx = self.overlay_mode_combo.findData(current_mode)
        if idx >= 0:
            self.overlay_mode_combo.setCurrentIndex(idx)
        form.addRow("Text Overlay:", self.overlay_mode_combo)

        # Custom Overlay Text Input
        self.custom_text_edit = QLineEdit(first_cfg.get("overlay_custom_text", ""))
        self.custom_text_edit.setPlaceholderText("Enter manual overlay text...")
        self.custom_text_edit.setEnabled(current_mode == "custom")
        self.overlay_mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Manual Overlay Text:", self.custom_text_edit)

        # 2e. Host Audio Playback Choice
        self.audio_combo = QComboBox()
        self.audio_combo.addItem("Muted on Host Computer", "mute")
        self.audio_combo.addItem("Play Audio on Host Computer (Exclusive - Mute Others)", "exclusive")
        self.audio_combo.addItem("Play Audio on Host Computer (Mix with Active Tabs)", "mix")
        if first_cfg.get("play_audio", False):
            self.audio_combo.setCurrentIndex(1)
        else:
            self.audio_combo.setCurrentIndex(0)
        form.addRow("Host Audio Output:", self.audio_combo)

        # Stream Stats Toggle
        self.show_stats_cb = QCheckBox("Show stream resolution and live FPS badges")
        self.show_stats_cb.setChecked(first_cfg.get("show_stats", True))
        form.addRow("Technical HUD:", self.show_stats_cb)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_custom_dir_toggled(self, checked: bool):
        self.dir_edit.setEnabled(checked)
        self.btn_browse.setEnabled(checked)
        if not checked:
            self.dir_edit.setText(DEFAULT_RECORD_DIR)

    def _on_mode_changed(self):
        is_custom = self.overlay_mode_combo.currentData() == "custom"
        self.custom_text_edit.setEnabled(is_custom)

    def _browse_dir(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Record Directory", self.dir_edit.text()
        )
        if folder:
            self.dir_edit.setText(folder)

    def get_settings_result(self) -> dict:
        use_custom = self.use_custom_dir_cb.isChecked()
        rec_dir = self.dir_edit.text().strip() if use_custom else DEFAULT_RECORD_DIR
        return {
            "title": self.title_edit.text().strip(),
            "use_custom_dir": use_custom,
            "record_dir": rec_dir or DEFAULT_RECORD_DIR,
            "overlay_mode": self.overlay_mode_combo.currentData(),
            "overlay_custom_text": self.custom_text_edit.text().strip(),
            "audio_action": self.audio_combo.currentData(),
            "show_stats": self.show_stats_cb.isChecked(),
        }


class VUMeterWidget(QWidget):
    """Stereo vertical audio meter with peak hold and smooth decay."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(16)
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

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
        self.left_level *= 0.90
        self.right_level *= 0.90
        self.left_peak = max(self.left_level, self.left_peak * 0.98)
        self.right_peak = max(self.right_level, self.right_peak * 0.98)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.width(), self.height()
        bar_w = max(2, (w - 6) // 2)

        painter.fillRect(0, 0, w, h, QColor(14, 14, 14))

        gradient = QLinearGradient(0, h, 0, 0)
        gradient.setColorAt(0.0, QColor(40, 180, 40))
        gradient.setColorAt(0.70, QColor(180, 200, 30))
        gradient.setColorAt(0.90, QColor(230, 140, 20))
        gradient.setColorAt(1.0, QColor(230, 30, 30))

        for i, (lvl, pk) in enumerate([(self.left_level, self.left_peak), (self.right_level, self.right_peak)]):
            x = 2 + i * (bar_w + 2)
            bar_h = int(lvl * h)
            painter.fillRect(x, h - bar_h, bar_w, bar_h, gradient)

            if pk > 0.02:
                peak_y = max(0, h - int(pk * h) - 2)
                painter.fillRect(x, peak_y, bar_w, 2, QColor(255, 255, 255))


class VideoCanvas(QWidget):
    """Draws the scaled NDI video frame and configurable HUD overlay."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(64, 36)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        # Allow mouse clicks on the canvas to pass straight to the parent VideoTile
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.current_frame = None
        self.overlay_text = ""
        self.show_stats = True
        self.source_name = ""
        self.stream_res = ""
        self.live_fps = 0.0
        self.is_recording = False
        self.is_paused = False
        self.accumulated_sec = 0.0
        self.last_resume_time = None
        self.timecode_str = "00:00:00"
        self.blink_state = True

        self.hud_timer = QTimer(self)
        self.hud_timer.timeout.connect(self._update_clock)
        self.hud_timer.start(250)

    def set_frame(self, img: QImage):
        self.current_frame = img
        self.update()

    def clear_frame(self):
        self.current_frame = None
        self.source_name = ""
        self.stream_res = ""
        self.live_fps = 0.0
        self.update()

    def set_overlay_config(self, text: str, show_stats: bool):
        self.overlay_text = text
        self.show_stats = show_stats
        self.update()

    def set_stream_info(self, name: str, width: int, height: int):
        self.source_name = name
        self.stream_res = f"{width}x{height}" if width and height else ""
        self.update()

    def set_live_fps(self, fps: float):
        self.live_fps = fps
        self.update()

    def set_recording(self, recording: bool):
        self.is_recording = recording
        self.is_paused = False
        self.accumulated_sec = 0.0
        if recording:
            self.last_resume_time = time.time()
            self.timecode_str = "00:00:00"
        else:
            self.last_resume_time = None
        self.update()

    def set_paused(self, paused: bool):
        if not self.is_recording:
            return
        if paused and not self.is_paused:
            if self.last_resume_time:
                self.accumulated_sec += time.time() - self.last_resume_time
            self.last_resume_time = None
            self.is_paused = True
        elif not paused and self.is_paused:
            self.last_resume_time = time.time()
            self.is_paused = False
        self.update()

    def _update_clock(self):
        self.blink_state = not self.blink_state
        if self.is_recording:
            total = self.accumulated_sec
            if not self.is_paused and self.last_resume_time:
                total += time.time() - self.last_resume_time
            self.timecode_str = str(timedelta(seconds=int(total))).zfill(8)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(12, 12, 12))

        # 1. Draw Video Frame scaled to fit available tile space
        if self.current_frame and not self.current_frame.isNull():
            scaled = self.current_frame.scaled(w - 4, h - 4, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2
            painter.drawImage(x, y, scaled)
        else:
            painter.setPen(QColor(105, 105, 105))
            painter.drawText(self.rect(), Qt.AlignCenter, "No Source Assigned\n(Click empty area to select tab)")

        # 2. Draw HUD Overlays
        font = QFont("Helvetica", 9, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        pad = 6
        margin = 8
        bg_brush = QColor(0, 0, 0, 180)
        text_h = fm.height()

        # Top-Left: Configurable Text Overlay
        top_left_parts = []
        if self.overlay_text:
            top_left_parts.append(self.overlay_text)
        if self.show_stats and self.stream_res:
            top_left_parts.append(f"[{self.stream_res}]")

        if top_left_parts:
            header = " ".join(top_left_parts)
            tw = fm.horizontalAdvance(header)
            box_tl = QRect(margin, margin, tw + pad * 2, text_h + pad)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg_brush)
            painter.drawRoundedRect(box_tl, 4, 4)
            painter.setPen(QColor(245, 245, 245))
            painter.drawText(box_tl, Qt.AlignCenter, header)

        # Top-Right: Live FPS
        if self.show_stats and self.source_name and self.live_fps > 0:
            fps_txt = f"{self.live_fps:.1f} fps"
            fw = fm.horizontalAdvance(fps_txt)
            box_tr = QRect(w - margin - fw - pad * 2, margin, fw + pad * 2, text_h + pad)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg_brush)
            painter.drawRoundedRect(box_tr, 4, 4)
            painter.setPen(QColor(0, 220, 80) if self.live_fps >= 28.0 else QColor(255, 170, 0))
            painter.drawText(box_tr, Qt.AlignCenter, fps_txt)

        # Bottom-Left: Recording / Paused Timecode
        if self.is_recording:
            status_label = "PAUSED" if self.is_paused else "REC"
            rec_txt = f"{status_label}  {self.timecode_str}"
            rw = fm.horizontalAdvance(rec_txt) + 14
            box_bl = QRect(margin, h - margin - text_h - pad, rw + pad * 2, text_h + pad)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(40, 25, 0, 215) if self.is_paused else QColor(35, 0, 0, 215))
            painter.drawRoundedRect(box_bl, 4, 4)

            if self.is_paused:
                painter.setBrush(QColor(255, 165, 0))
                painter.drawEllipse(box_bl.x() + pad, box_bl.y() + (box_bl.height() - 8) // 2, 8, 8)
            elif self.blink_state:
                painter.setBrush(QColor(240, 20, 20))
                painter.drawEllipse(box_bl.x() + pad, box_bl.y() + (box_bl.height() - 8) // 2, 8, 8)

            painter.setPen(QColor(255, 255, 255))
            t_rect = QRect(box_bl.x() + pad + 12, box_bl.y(), box_bl.width() - pad - 12, box_bl.height())
            painter.drawText(t_rect, Qt.AlignVCenter | Qt.AlignLeft, rec_txt)


class VideoTile(QFrame):
    tile_clicked = Signal(int)
    tile_double_clicked = Signal()
    settings_requested = Signal(int)
    exclusive_audio_requested = Signal(int)

    def __init__(self, tile_id: int, detected_encoder: str = "libx264", initial_config: dict = None):
        super().__init__()
        self.tile_id = tile_id
        self.detected_encoder = detected_encoder
        self.worker = None
        self.audio_output = HostAudioOutput()

        self.is_recording = False
        self.is_paused = False
        self.is_selected = False

        self.config = {
            "title": f"Tile {tile_id + 1}",
            "use_custom_dir": False,
            "record_dir": DEFAULT_RECORD_DIR,
            "overlay_mode": "tab_name",  # "none", "tab_name", "custom"
            "overlay_custom_text": "",
            "play_audio": False,
            "show_stats": True,
        }
        if initial_config:
            self.config.update(initial_config)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 6, 8, 8)
        main_layout.setSpacing(5)

        # Header Bar: Title + Audio Button + Settings Gear Button
        header_bar = QHBoxLayout()
        header_bar.setContentsMargins(2, 0, 2, 0)
        header_bar.setSpacing(6)

        self.title_label = QLabel(self.config["title"])
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.title_label.setStyleSheet("color: #f0f0f0; font-weight: bold; font-size: 12px;")
        header_bar.addWidget(self.title_label, stretch=1)

        # Host Audio Quick Indicator / Toggle Button
        self.btn_audio = QPushButton("🔇")
        self.btn_audio.setFixedSize(26, 22)
        self.btn_audio.setToolTip("Toggle Host Computer Audio Playback for this Tab")
        self.btn_audio.clicked.connect(self._toggle_quick_audio)
        header_bar.addWidget(self.btn_audio, stretch=0)

        # Settings Button (⚙)
        self.btn_config = QPushButton("⚙")
        self.btn_config.setFixedSize(26, 22)
        self.btn_config.setToolTip("Open Slot Settings (applies to all selected tabs)")
        self.btn_config.setStyleSheet("""
            QPushButton {
                background-color: #2b2b2b;
                color: #dddddd;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                font-size: 13px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #404040;
                color: #ffffff;
            }
        """)
        self.btn_config.clicked.connect(lambda: self.settings_requested.emit(self.tile_id))
        header_bar.addWidget(self.btn_config, stretch=0)

        main_layout.addLayout(header_bar, stretch=0)

        # Viewport + Audio Meter
        view_layout = QHBoxLayout()
        view_layout.setSpacing(4)
        self.canvas = VideoCanvas()
        view_layout.addWidget(self.canvas, stretch=1)

        self.vu_meter = VUMeterWidget()
        view_layout.addWidget(self.vu_meter, stretch=0)
        main_layout.addLayout(view_layout, stretch=1)

        # Bottom Controls: Source Selector + Record + Pause
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(5)
        self.source_selector = QComboBox()
        self.source_selector.addItem("--- Select Source ---")
        ctrl_bar.addWidget(self.source_selector, stretch=1)

        self.btn_record = QPushButton("Record")
        self.btn_record.setEnabled(False)
        self.btn_record.setCheckable(True)
        self.btn_record.clicked.connect(self.toggle_recording)
        ctrl_bar.addWidget(self.btn_record, stretch=0)

        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setEnabled(False)
        self.btn_pause.setCheckable(True)
        self.btn_pause.clicked.connect(self.toggle_pause)
        ctrl_bar.addWidget(self.btn_pause, stretch=0)

        main_layout.addLayout(ctrl_bar, stretch=0)
        self.source_selector.activated.connect(self.on_source_selected)

        self._apply_config_to_ui()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tile_clicked.emit(self.tile_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tile_double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        """
        Custom paints the tile background and enforces border rules:
        - Recording active -> Red border (#FF1E00)
        - Selected tab -> Yellow border (#FFD700)
        - If both recording and selected -> Red outer border + Yellow inner ring
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()

        # Dark slate tile fill
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(28, 28, 30))
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        if self.is_recording and self.is_selected:
            # Red outer active recording border + Yellow inner selection border
            pen_red = QPen(QColor("#FF1E00"), 4)
            painter.setPen(pen_red)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(2, 2, w - 4, h - 4, 6, 6)

            pen_yellow = QPen(QColor("#FFD700"), 2)
            painter.setPen(pen_yellow)
            painter.drawRoundedRect(5, 5, w - 10, h - 10, 4, 4)
        elif self.is_recording:
            pen_red = QPen(QColor("#FF1E00"), 4)
            painter.setPen(pen_red)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(2, 2, w - 4, h - 4, 6, 6)
        elif self.is_selected:
            pen_yellow = QPen(QColor("#FFD700"), 3)
            painter.setPen(pen_yellow)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(2, 2, w - 4, h - 4, 6, 6)
        else:
            pen_idle = QPen(QColor("#38383C"), 1)
            painter.setPen(pen_idle)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

    def set_selected_state(self, selected: bool):
        self.is_selected = selected
        self.update()

    def _toggle_quick_audio(self):
        new_state = not self.config.get("play_audio", False)
        self.set_host_audio(new_state)

    def set_host_audio(self, enabled: bool):
        self.config["play_audio"] = enabled
        if self.worker:
            self.worker.set_host_audio_enabled(enabled)
        if not enabled:
            self.audio_output.stop()

        if enabled:
            self.btn_audio.setText("🔊")
            self.btn_audio.setStyleSheet(
                "QPushButton { background-color: #145228; color: #ffffff; "
                "border: 1px solid #00CC44; border-radius: 4px; font-size: 12px; padding: 0px; }"
            )
        else:
            self.btn_audio.setText("🔇")
            self.btn_audio.setStyleSheet(
                "QPushButton { background-color: #2b2b2b; color: #888888; "
                "border: 1px solid #444444; border-radius: 4px; font-size: 12px; padding: 0px; }"
            )

    def apply_batch_settings(self, settings: dict):
        if settings.get("title"):
            self.config["title"] = settings["title"]
        self.config["use_custom_dir"] = settings["use_custom_dir"]
        self.config["record_dir"] = settings["record_dir"]
        self.config["overlay_mode"] = settings["overlay_mode"]
        self.config["overlay_custom_text"] = settings["overlay_custom_text"]
        self.config["show_stats"] = settings["show_stats"]
        self._apply_config_to_ui()

    def _apply_config_to_ui(self):
        self.title_label.setText(self.config["title"])
        mode = self.config["overlay_mode"]
        if mode == "none":
            overlay_str = ""
        elif mode == "custom":
            overlay_str = self.config["overlay_custom_text"]
        else:
            overlay_str = self.config["title"]

        self.canvas.set_overlay_config(overlay_str, self.config.get("show_stats", True))
        self.set_host_audio(self.config.get("play_audio", False))

    def get_config(self) -> dict:
        return dict(self.config)

    def get_tile_name(self) -> str:
        return self.config.get("title", f"Tile_{self.tile_id + 1}")

    def is_assigned(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def update_source_list(self, sources: list[str]):
        current = self.source_selector.currentText()
        self.source_selector.blockSignals(True)
        self.source_selector.clear()
        self.source_selector.addItem("--- Select Source ---")
        for src in sources:
            self.source_selector.addItem(src)
        idx = self.source_selector.findText(current)
        if idx >= 0:
            self.source_selector.setCurrentIndex(idx)
        self.source_selector.blockSignals(False)

    def on_source_selected(self):
        selected = self.source_selector.currentText()
        if self.worker:
            self.stop_tile_recording()
            self.worker.stop()
            self.worker = None
            self.audio_output.stop()
            self.btn_record.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.vu_meter.update_levels(0.0, 0.0)
            self.canvas.clear_frame()

        if selected != "--- Select Source ---":
            short_name = selected.split("(")[0].strip() if "(" in selected else selected
            self.canvas.set_stream_info(short_name, 0, 0)

            self.worker = NDIStreamWorker(selected, selected_encoder=self.detected_encoder)
            self.worker.set_host_audio_enabled(self.config.get("play_audio", False))
            self.worker.frame_ready.connect(self.canvas.set_frame)
            self.worker.audio_levels.connect(self.vu_meter.update_levels)
            self.worker.stream_specs.connect(self.on_specs_updated)
            self.worker.audio_pcm_ready.connect(self.audio_output.write_pcm)
            self.worker.start()
            self.btn_record.setEnabled(True)

    @Slot(int, int, float)
    def on_specs_updated(self, w: int, h: int, fps: float):
        selected = self.source_selector.currentText()
        short_name = selected.split("(")[0].strip() if "(" in selected else selected
        self.canvas.set_stream_info(short_name, w, h)
        self.canvas.set_live_fps(fps)

    def start_tile_recording(self, session_tag: str = None) -> bool:
        if not self.is_assigned() or self.is_recording:
            return False

        if self.config.get("use_custom_dir") and self.config.get("record_dir"):
            output_dir = self.config["record_dir"]
        else:
            output_dir = DEFAULT_RECORD_DIR
        os.makedirs(output_dir, exist_ok=True)

        timestamp = session_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_tile = "".join(c if c.isalnum() else "_" for c in self.get_tile_name()).strip("_")
        safe_src = "".join(c for c in self.source_selector.currentText() if c.isalnum() or c in ("-", "_")).strip()
        filename = f"{timestamp}_{safe_tile or 'Tile'}_{safe_src or 'Stream'}.mp4"
        filepath = os.path.join(output_dir, filename)

        self.worker.start_recording(filepath)
        self.is_recording = True
        self.is_paused = False
        self.canvas.set_recording(True)

        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(True)
        self.btn_record.setText("Stop")
        self.btn_record.setStyleSheet("background-color: #E51400; color: white; font-weight: bold;")
        self.btn_record.blockSignals(False)

        self.btn_pause.blockSignals(True)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setChecked(False)
        self.btn_pause.setText("Pause")
        self.btn_pause.setStyleSheet("")
        self.btn_pause.blockSignals(False)

        self.update()
        return True

    def set_tile_paused(self, paused: bool):
        if not self.is_recording or not self.worker:
            return
        self.is_paused = paused
        self.worker.set_paused(paused)
        self.canvas.set_paused(paused)

        self.btn_pause.blockSignals(True)
        self.btn_pause.setChecked(paused)
        if paused:
            self.btn_pause.setText("Resume")
            self.btn_pause.setStyleSheet("background-color: #D98200; color: white; font-weight: bold;")
        else:
            self.btn_pause.setText("Pause")
            self.btn_pause.setStyleSheet("")
        self.btn_pause.blockSignals(False)
        self.update()

    def stop_tile_recording(self):
        if not self.is_recording:
            return
        if self.worker:
            self.worker.stop_recording()

        self.is_recording = False
        self.is_paused = False
        self.canvas.set_recording(False)

        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(False)
        self.btn_record.setText("Record")
        self.btn_record.setStyleSheet("")
        self.btn_record.blockSignals(False)

        self.btn_pause.blockSignals(True)
        self.btn_pause.setChecked(False)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("Pause")
        self.btn_pause.setStyleSheet("")
        self.btn_pause.blockSignals(False)

        self.update()

    def toggle_recording(self, checked: bool):
        if checked:
            self.start_tile_recording()
        else:
            self.stop_tile_recording()

    def toggle_pause(self, checked: bool):
        self.set_tile_paused(checked)

    def cleanup(self):
        self.stop_tile_recording()
        self.audio_output.stop()
        if self.worker:
            self.worker.stop()
            self.worker = None
