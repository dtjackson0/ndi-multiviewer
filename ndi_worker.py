import time
import socket
import platform
import threading
import subprocess
import numpy as np
import NDIlib as ndi
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


def get_ndi_name(source_obj) -> str:
    """Compatible with both ndi-python v5 (p_ndi_name) and v6 (ndi_name)."""
    return getattr(source_obj, "ndi_name", getattr(source_obj, "p_ndi_name", ""))


def set_ndi_name(source_obj, name: str):
    if hasattr(source_obj, "ndi_name"):
        source_obj.ndi_name = name
    else:
        source_obj.p_ndi_name = name


def detect_best_h264_encoder() -> str:
    """Probes FFmpeg for functional hardware-accelerated H.264 encoders."""
    creation_flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
    try:
        output = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creation_flags
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
            "ffmpeg", "-y", "-f", "lavfi", "-i", "nullsrc=s=128x128:d=0.1",
            "-c:v", enc, "-f", "null", "-"
        ]
        res = subprocess.run(
            test_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags
        )
        if res.returncode == 0:
            return enc

    return "libx264"


class NDIStreamWorker(QThread):
    frame_ready = Signal(QImage)
    audio_levels = Signal(float, float)
    stream_specs = Signal(int, int, float)        # (width, height, fps)
    audio_pcm_ready = Signal(bytes, int, int)     # (interleaved_f32_bytes, sample_rate, channels)

    def __init__(self, source_name: str, selected_encoder: str = "libx264"):
        super().__init__()
        self.source_name = source_name
        self.encoder = selected_encoder
        self.running = True
        self.recording = False
        self.paused = False
        self.play_host_audio = False
        self.record_process = None

        self.stream_width = 1920
        self.stream_height = 1080
        self.current_fps = 30.0
        self.has_audio = False
        self.sample_rate = 48000
        self.audio_channels = 2

        self.frame_count = 0
        self.last_fps_calc = time.time()
        self.last_audio_emit = 0.0

        self.v_server = None
        self.a_server = None
        self.v_conn = None
        self.a_conn = None

    def _create_server_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        s.settimeout(5.0)
        return s, s.getsockname()[1]

    def set_paused(self, paused: bool):
        self.paused = paused

    def set_host_audio_enabled(self, enabled: bool):
        self.play_host_audio = enabled

    def start_recording(self, output_filename: str):
        if self.recording:
            return

        self.paused = False
        self.v_server, v_port = self._create_server_socket()
        use_audio = self.has_audio
        if use_audio:
            self.a_server, a_port = self._create_server_socket()

        fps_int = max(1, int(round(self.current_fps))) if self.current_fps > 5 else 30

        cmd = [
            "ffmpeg", "-y",
            "-probesize", "32", "-analyzeduration", "0",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr0",
            "-s", f"{self.stream_width}x{self.stream_height}",
            "-r", str(fps_int),
            "-i", f"tcp://127.0.0.1:{v_port}",
        ]

        if use_audio:
            cmd += [
                "-probesize", "32", "-analyzeduration", "0",
                "-f", "f32le",
                "-ar", str(self.sample_rate),
                "-ac", str(self.audio_channels),
                "-i", f"tcp://127.0.0.1:{a_port}",
            ]

        cmd += ["-c:v", self.encoder]

        if self.encoder == "h264_nvenc":
            cmd += ["-preset", "p4", "-tune", "ll", "-cq", "20"]
        elif self.encoder == "h264_qsv":
            cmd += ["-preset", "veryfast", "-global_quality", "20"]
        elif self.encoder == "h264_videotoolbox":
            cmd += ["-realtime", "true", "-q:v", "65"]
        else:
            cmd += ["-preset", "veryfast", "-crf", "20"]

        cmd += ["-pix_fmt", "yuv420p"]

        if use_audio:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]

        cmd.append(output_filename)

        creation_flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        self.record_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags
        )

        def _accept_pipes():
            try:
                self.v_conn, _ = self.v_server.accept()
                if use_audio and self.a_server:
                    self.a_conn, _ = self.a_server.accept()
                self.recording = True
            except Exception:
                self.stop_recording()

        threading.Thread(target=_accept_pipes, daemon=True).start()

    def stop_recording(self):
        self.recording = False
        self.paused = False
        for sock in [self.v_conn, self.a_conn, self.v_server, self.a_server]:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self.v_conn = self.a_conn = self.v_server = self.a_server = None

        if self.record_process:
            try:
                self.record_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.record_process.kill()
            self.record_process = None

    @staticmethod
    def _calculate_channel_level(samples: np.ndarray) -> float:
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
        set_ndi_name(source, self.source_name)
        ndi.recv_connect(recv, source)

        while self.running:
            type_, video_frame, audio_frame, _ = ndi.recv_capture_v2(recv, 50)

            if type_ == ndi.FRAME_TYPE_VIDEO:
                w = video_frame.xres
                h = video_frame.yres
                self.stream_width = w
                self.stream_height = h

                data = np.ascontiguousarray(video_frame.data)

                self.frame_count += 1
                now = time.time()
                elapsed = now - self.last_fps_calc
                if elapsed >= 1.0:
                    self.current_fps = self.frame_count / elapsed
                    self.frame_count = 0
                    self.last_fps_calc = now
                    self.stream_specs.emit(w, h, self.current_fps)

                # Write video frame only if recording AND not paused
                if self.recording and not self.paused and self.v_conn:
                    try:
                        self.v_conn.sendall(data.tobytes())
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        self.stop_recording()

                qimg = QImage(data.data, w, h, w * 4, QImage.Format_RGB32)
                self.frame_ready.emit(qimg.copy())
                ndi.recv_free_video_v2(recv, video_frame)

            elif type_ == ndi.FRAME_TYPE_AUDIO:
                self.has_audio = True
                self.sample_rate = audio_frame.sample_rate
                self.audio_channels = audio_frame.no_channels
                audio_data = np.copy(audio_frame.data)

                interleaved_bytes = None
                if (self.recording and not self.paused and self.a_conn) or self.play_host_audio:
                    interleaved_bytes = audio_data.T.astype(np.float32).tobytes()

                # Write audio samples to FFmpeg if recording and not paused
                if self.recording and not self.paused and self.a_conn and interleaved_bytes:
                    try:
                        self.a_conn.sendall(interleaved_bytes)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        self.stop_recording()

                # Send audio samples to host computer output if enabled
                if self.play_host_audio and interleaved_bytes:
                    self.audio_pcm_ready.emit(interleaved_bytes, self.sample_rate, self.audio_channels)

                now = time.time()
                if now - self.last_audio_emit > 0.033:
                    self.last_audio_emit = now
                    left = self._calculate_channel_level(audio_data[0]) if self.audio_channels >= 1 else 0.0
                    right = self._calculate_channel_level(audio_data[1]) if self.audio_channels >= 2 else left
                    self.audio_levels.emit(left, right)

                ndi.recv_free_audio_v2(recv, audio_frame)

        self.stop_recording()
        ndi.recv_destroy(recv)

    def stop(self):
        self.running = False
        self.wait()
