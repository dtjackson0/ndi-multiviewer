import os
import time
from datetime import datetime, timedelta
from PySide6.QtCore import Qt, QRect, QTimer, Signal, Slot
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QFont, QFontMetrics, QImage
from PySide6.QtWidgets import (
    QWidget, QGroupBox, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton
)
from ndi_worker import NDIStreamWorker


class VUMeterWidget(QWidget):
    """Stereo vertical audio meter with peak hold and smooth decay."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(22)
        self.setMinimumHeight(120)

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
        bar_w = (w - 6) // 2

        painter.fillRect(0, 0, w, h, QColor(18, 18, 18))

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
    """Draws the scaled NDI video frame, tally border, and on-screen HUD overlay."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 135)

        self.current_frame = None
        self.source_name = ""
        self.stream_res = ""
        self.live_fps = 0.0
        self.is_recording = False
        self.is_focused = False
        self.rec_start_time = None
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

    def set_focused(self, focused: bool):
        self.is_focused = focused
        self.update()

    def _update_clock(self):
        self.blink_state = not self.blink_state
        if self.is_recording and self.rec_start_time:
            elapsed = int(time.time() - self.rec_start_time)
            self.timecode_str = str(timedelta(seconds=elapsed)).zfill(8)
            self.update()
        elif self.is_recording:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(16, 16, 16))

        # 1. Draw Video Frame (letterboxed/pillarboxed) or Idle Text
        if self.current_frame and not self.current_frame.isNull():
            scaled = self.current_frame.scaled(w - 6, h - 6, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2
            painter.drawImage(x, y, scaled)
        else:
            painter.setPen(QColor(110, 110, 110))
            painter.drawText(self.rect(), Qt.AlignCenter, "No Source Assigned")

        # 2. Draw HUD Overlays if active
        if self.source_name:
            font = QFont("Helvetica", 9, QFont.Bold)
            painter.setFont(font)
            fm = QFontMetrics(font)
            pad = 6
            margin = 10
            bg_brush = QColor(0, 0, 0, 175)
            text_h = fm.height()

            # Top-Left: Source Name & Resolution
            header = f"{self.source_name} [{self.stream_res}]" if self.stream_res else self.source_name
            tw = fm.horizontalAdvance(header)
            box_tl = QRect(margin, margin, tw + pad * 2, text_h + pad)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg_brush)
            painter.drawRoundedRect(box_tl, 4, 4)
            painter.setPen(QColor(240, 240, 240))
            painter.drawText(box_tl, Qt.AlignCenter, header)

            # Top-Right: Live FPS
            if self.live_fps > 0:
                fps_txt = f"{self.live_fps:.1f} fps"
                fw = fm.horizontalAdvance(fps_txt)
                box_tr = QRect(w - margin - fw - pad * 2, margin, fw + pad * 2, text_h + pad)
                painter.setPen(Qt.NoPen)
                painter.setBrush(bg_brush)
                painter.drawRoundedRect(box_tr, 4, 4)
                painter.setPen(QColor(0, 220, 80) if self.live_fps >= 28.0 else QColor(255, 170, 0))
                painter.drawText(box_tr, Qt.AlignCenter, fps_txt)

            # Bottom-Left: Recording Timecode
            if self.is_recording:
                rec_txt = f"REC  {self.timecode_str}"
                rw = fm.horizontalAdvance(rec_txt) + 14
                box_bl = QRect(margin, h - margin - text_h - pad, rw + pad * 2, text_h + pad)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(30, 0, 0, 210))
                painter.drawRoundedRect(box_bl, 4, 4)

                if self.blink_state:
                    painter.setBrush(QColor(235, 20, 20))
                    painter.drawEllipse(box_bl.x() + pad, box_bl.y() + (box_bl.height() - 8) // 2, 8, 8)

                painter.setPen(QColor(255, 255, 255))
                t_rect = QRect(box_bl.x() + pad + 12, box_bl.y(), box_bl.width() - pad - 12, box_bl.height())
                painter.drawText(t_rect, Qt.AlignVCenter | Qt.AlignLeft, rec_txt)

        # 3. Draw Broadcast Tally Border
        if self.is_recording:
            border_color = QColor("#E51400")  # Program / Recording Red
        elif self.is_focused:
            border_color = QColor("#00CC44")  # Preview / Focused Green
        else:
            border_color = QColor("#2E2E2E")  # Idle Gray

        pen = painter.pen()
        pen.setColor(border_color)
        pen.setWidth(3)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(1, 1, w - 2, h - 2)


class VideoTile(QGroupBox):
    tile_focused = Signal(int)

    def __init__(self, tile_id: int, detected_encoder: str = "libx264"):
        super().__init__(f"Tile {tile_id + 1}")
        self.tile_id = tile_id
        self.detected_encoder = detected_encoder
        self.worker = None
        self.is_recording = False

        main_layout = QVBoxLayout(self)

        # Viewport + Audio Meter
        view_layout = QHBoxLayout()
        self.canvas = VideoCanvas()
        view_layout.addWidget(self.canvas, stretch=1)

        self.vu_meter = VUMeterWidget()
        view_layout.addWidget(self.vu_meter, stretch=0)
        main_layout.addLayout(view_layout, stretch=1)

        # Bottom Controls
        ctrl_bar = QHBoxLayout()
        self.source_selector = QComboBox()
        self.source_selector.addItem("--- Select Source ---")
        ctrl_bar.addWidget(self.source_selector, stretch=1)

        self.btn_record = QPushButton("Record")
        self.btn_record.setEnabled(False)
        self.btn_record.setCheckable(True)
        self.btn_record.clicked.connect(self.toggle_recording)
        ctrl_bar.addWidget(self.btn_record)

        main_layout.addLayout(ctrl_bar)
        self.source_selector.activated.connect(self.on_source_selected)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tile_focused.emit(self.tile_id)
        super().mousePressEvent(event)

    def set_focus_state(self, focused: bool):
        self.canvas.set_focused(focused)

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
            self.btn_record.setEnabled(False)
            self.vu_meter.update_levels(0.0, 0.0)
            self.canvas.clear_frame()

        if selected != "--- Select Source ---":
            short_name = selected.split("(")[0].strip() if "(" in selected else selected
            self.canvas.set_stream_info(short_name, 0, 0)

            self.worker = NDIStreamWorker(selected, selected_encoder=self.detected_encoder)
            self.worker.frame_ready.connect(self.canvas.set_frame)
            self.worker.audio_levels.connect(self.vu_meter.update_levels)
            self.worker.stream_specs.connect(self.on_specs_updated)
            self.worker.start()
            self.btn_record.setEnabled(True)

    @Slot(int, int, float)
    def on_specs_updated(self, w: int, h: int, fps: float):
        selected = self.source_selector.currentText()
        short_name = selected.split("(")[0].strip() if "(" in selected else selected
        self.canvas.set_stream_info(short_name, w, h)
        self.canvas.set_live_fps(fps)

    def start_tile_recording(self, output_dir: str = "captures", session_tag: str = None) -> bool:
        if not self.is_assigned() or self.is_recording:
            return False

        os.makedirs(output_dir, exist_ok=True)
        timestamp = session_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_src = "".join(c for c in self.source_selector.currentText() if c.isalnum() or c in ("-", "_")).strip()
        filename = f"{timestamp}_Tile{self.tile_id + 1}_{safe_src or 'Stream'}.mp4"
        filepath = os.path.join(output_dir, filename)

        self.worker.start_recording(filepath)
        self.is_recording = True
        self.canvas.set_recording(True)

        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(True)
        self.btn_record.setText("Stop Rec")
        self.btn_record.setStyleSheet("background-color: #E51400; color: white; font-weight: bold;")
        self.btn_record.blockSignals(False)
        return True

    def stop_tile_recording(self):
        if not self.is_recording:
            return
        if self.worker:
            self.worker.stop_recording()

        self.is_recording = False
        self.canvas.set_recording(False)

        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(False)
        self.btn_record.setText("Record")
        self.btn_record.setStyleSheet("")
        self.btn_record.blockSignals(False)

    def toggle_recording(self, checked: bool):
        if checked:
            self.start_tile_recording()
        else:
            self.stop_tile_recording()

    def cleanup(self):
        self.stop_tile_recording()
        if self.worker:
            self.worker.stop()
            self.worker = None