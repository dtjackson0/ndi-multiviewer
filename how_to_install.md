# How-To Guide: NDI MultiView & ISO Recorder

This guide outlines the prerequisites, installation instructions, and launch steps for running the NDI MultiView & ISO Recorder on macOS or Windows 11.

---

## 1. Prerequisites

Before installing the application dependencies, ensure the following tools and runtimes are installed on your system:

* **NDI Runtime / NDI Tools:**
  * Download and install **NDI Tools** (or the NDI SDK) from the official website.
  * *macOS Apple Silicon note:* Ensure `libndi.dylib` is properly symlinked to `/usr/local/lib`:
    ```bash
    sudo mkdir -p /usr/local/lib
    sudo ln -sf "/Library/NDI/lib/macOS/libndi.dylib" /usr/local/lib/libndi.dylib
    ```
* **FFmpeg:**
  * Must be installed and accessible globally in your system `PATH`.
  * *macOS:* Install via Homebrew using `brew install ffmpeg`.
  * *Windows 11:* Install via winget using `winget install Gyan.FFmpeg`.
* **Python 3.10 or higher:**
  * *macOS:* Install via `brew install python`.
  * *Windows 11:* Install via `winget install Python.Python.3.12`.

---

## 2. Installation Instructions

Follow these steps to set up the project folder, virtual environment, and Python packages:

### macOS
1. Open your Terminal and navigate to your project directory:
   ```bash
   cd ~/ndi-multiviewer