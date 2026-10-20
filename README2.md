# NDI MultiView & ISO Recorder

A cross-platform NDI multiviewer and multi-channel ISO recorder built with Python, PySide6, and FFmpeg.

## Features
- Dynamic $R \times C$ grid layout
- Hardware-accelerated H.264 recording (`NVENC`, `QuickSync`, `VideoToolbox`, `AMF`, `x264` fallback)
- Synchronized audio capture with real-time stereo VU meters
- Program (Red) and Preview (Green) broadcast tally borders
- On-screen HUD (Source name, resolution, live FPS, and recording timecode)
- Master Record trigger for simultaneous multi-camera ISO takes

## Prerequisites
1. **NDI Runtime / Tools**: Install from [ndi.video](https://ndi.video)
2. **FFmpeg**: Installed and added to system `PATH`
   - **macOS**: `brew install ffmpeg`
   - **Windows 11**: `winget install Gyan.FFmpeg`

## Quick Start
```bash
python -m venv venv
# macOS: source venv/bin/activate
# Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
