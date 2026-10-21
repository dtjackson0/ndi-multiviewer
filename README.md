# NDI MultiView & ISO Recorder

A cross-platform desktop application built with Python, PySide6 (Qt6), and FFmpeg for viewing multiple NDI network video streams in a customizable grid and recording isolated (ISO) audio/video feeds to individual `.mp4` files.

---

## Key Features

* **Desktop-Filling Dynamic Grid ($R \times C$):** Configurable from $1 \times 1$ up to $6 \times 6$. Tiles automatically scale to fill the available desktop geometry while preserving video aspect ratios.
* **Carbon Fiber Interface:** Procedural diagonal-weave carbon fiber texture around and between all grid slots.
* **Hardware-Accelerated ISO Recording:** Automatically probes FFmpeg at startup for GPU encoders (`h264_nvenc`, `h264_qsv`, `h264_videotoolbox`, `h264_amf`) with automatic fallback to `libx264`.
* **Pause & Resume Recording:** Pause and resume individual tile recordings or use **Pause All** during a Master Record session without splitting or closing the output `.mp4` file.
* **Synchronized Audio & Stereo VU Meters:** Real-time Left/Right RMS dBFS audio level meters with peak-hold indicators on every tile, plus synchronized AAC audio muxing into recorded files.
* **Host Computer Audio Monitoring:** Route any tile's NDI audio to the host computer's speakers/headphones (Exclusive solo mode, Mix mode, or Muted) via the quick `🔊`/`🔇` toggle or Slot Settings.
* **Multi-Tab Selection & Batch Configuration:**
  * **Single-click** any tab to select/deselect it (**Yellow border**).
  * **Double-click** any tab to deselect all tabs.
  * Click **Configure (`⚙`)** on any selected tab to batch-edit settings across all selected tabs at once.
* **Broadcast Tally Borders & HUD Overlay:**
  * **Red Border:** Active recording tab.
  * **Yellow Border:** Selected tab for settings adjustment.
  * **Configurable HUD:** Choose between *Not Shown*, *Use Tab Name*, or *Manually Entered Text*, plus optional live resolution and framerate (`fps`) badges and an elapsed `REC` / `PAUSED` timecode counter.

---

## Project Structure

```text
ndi-multiviewer/
├── main.py            # Main window, toolbar, R×C grid manager, and carbon fiber container
├── ui_components.py   # VideoTile, VideoCanvas HUD, VUMeterWidget, HostAudioOutput, and SlotSettingsDialog
├── ndi_worker.py      # Background NDI receiver threads, GPU encoder probe, and FFmpeg TCP sockets
├── requirements.txt   # Python package dependencies
└── captures/          # Default output directory for recorded .mp4 files (created automatically)
