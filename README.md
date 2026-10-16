# NDI Multiviewer & ISO Recorder

A high-performance, cross-platform Python desktop application for real-time NDI (Network Device Interface) video grid monitoring, audio metering, and synchronized multi-camera ISO recording to disk.

Built with **PySide6 (Qt6)**, **NDI SDK (`ndi-python`)**, **NumPy**, and **FFmpeg**.

---

## 🌟 Key Features

- **Dynamic Grid Layouts**: Instantly switch between custom grid dimensions ($1 \times 1$, $2 \times 2$, $3 \times 3$, or up to $4 \times 4$).
- **Automatic Source Discovery**: Scans the local subnet for active NDI video senders and updates source selectors on the fly.
- **Hardware GPU Acceleration**: Auto-probes system capabilities at startup to leverage GPU encoders (**NVIDIA NVENC**, **Intel QuickSync**, **Apple Silicon VideoToolbox**, or **AMD AMF**) with software (`libx264`) fallback.
- **Synchronized ISO Multi-Cam Recording**: Includes a **🔴 Master Record ALL** controller that arms all active feeds under a unified session timestamp tag (e.g., `Take_20261016_120000_Tile1_CAM-A.mp4`) for instant multi-track alignment in Premiere Pro or DaVinci Resolve.
- **Broadcast Tally System**:
  - **Program Tally (Red)**: Active recording indicator.
  - **Preview Tally (Green)**: Tile mouse selection/focus indicator.
  - **Idle (Dark Gray)**: Unselected background state.
- **Real-Time Stereo VU Meters**: Custom twin-bar vertical level meters with broadcast dBFS conversion, nominal thresholds, and peak-hold ballistics decay.
- **On-Screen Display (HUD Overlay)**: Subclassed video viewport rendering live source resolution, real-time input FPS counters, and blinking elapsed recording timecode.

---

## 📐 System Architecture

```mermaid
flowchart TD
  subgraph Network["NDI Subnet"]
    NDI_Feed["Active NDI Stream"]
  end

  subgraph WorkerThread["NDIStreamWorker (QThread)"]
    NDI_Recv["NDI Capture Engine"]
    Audio_Calc["Stereo dBFS Metering"]
    FPS_Calc["FPS Benchmark"]
    V_Sock["Video Local TCP Socket"]
    A_Sock["Audio Local TCP Socket"]
  end

  subgraph EncoderProcess["FFmpeg Subprocess"]
    FFmpeg["FFmpeg CLI (Hardware Offload)"]
    File_Out["ISO Track MP4"]
  end

  subgraph UI["PySide6 Main Window"]
    Canvas["VideoCanvas Viewport + HUD"]
    VU["VUMeterWidget"]
    Tally["Border Tally State"]
  end

  NDI_Feed -->|"Raw BGRX & PCM Float"| NDI_Recv
  NDI_Recv -->|"Raw BGR0 Bytes"| V_Sock
  NDI_Recv -->|"32-bit Float LE PCM"| A_Sock
  
  NDI_Recv -->|"QImage"| Canvas
  Audio_Calc -->|"audio_levels (~30Hz)"| VU
  FPS_Calc -->|"stream_specs"| Canvas

  V_Sock -->|"tcp://127.0.0.1:portA"| FFmpeg
  A_Sock -->|"tcp://127.0.0.1:portB"| FFmpeg
  FFmpeg --> File_Out

  Canvas --> Tally
