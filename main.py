import sys
from datetime import datetime
import NDIlib as ndi
from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QGridLayout, QToolBar, QLabel, QSpinBox, QPushButton
)
from ndi_worker import detect_best_h264_encoder
from ui_components import VideoTile


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NDI MultiView & ISO Recorder")
        self.resize(1280, 760)

        if not ndi.initialize():
            raise SystemError("Could not initialize NDI SDK. Verify NDI Runtime is installed.")
        self.find_instance = ndi.find_create_v2()

        self.detected_encoder = detect_best_h264_encoder()
        self.statusBar().showMessage(f"Hardware Video Encoder: {self.detected_encoder}")

        self.tiles = []
        self.focused_tile_id = None

        self._setup_toolbar()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        main_layout.addWidget(self.grid_container, stretch=1)

        self.rebuild_grid()

    def _setup_toolbar(self):
        toolbar = QToolBar("Controls")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel("  Grid Rows: "))
        self.row_spin = QSpinBox()
        self.row_spin.setRange(1, 4)
        self.row_spin.setValue(2)
        toolbar.addWidget(self.row_spin)

        toolbar.addWidget(QLabel("  Cols: "))
        self.col_spin = QSpinBox()
        self.col_spin.setRange(1, 4)
        self.col_spin.setValue(2)
        toolbar.addWidget(self.col_spin)

        btn_grid = QPushButton("Apply Grid")
        btn_grid.clicked.connect(self.rebuild_grid)
        toolbar.addWidget(btn_grid)

        toolbar.addSeparator()

        btn_discover = QPushButton("Discover NDI Sources")
        btn_discover.clicked.connect(self.poll_ndi_sources)
        toolbar.addWidget(btn_discover)

        toolbar.addSeparator()

        self.btn_master_rec = QPushButton("Master Record ALL")
        self.btn_master_rec.setCheckable(True)
        self._style_master_button(False)
        self.btn_master_rec.clicked.connect(self.toggle_master_record)
        toolbar.addWidget(self.btn_master_rec)

    def _style_master_button(self, active: bool):
        if active:
            self.btn_master_rec.setText("Stop Master Record")
            self.btn_master_rec.setStyleSheet(
                "QPushButton { background-color: #E51400; color: white; font-weight: bold; "
                "padding: 6px 14px; border: 1px solid white; border-radius: 4px; }"
            )
        else:
            self.btn_master_rec.setText("Master Record ALL")
            self.btn_master_rec.setStyleSheet(
                "QPushButton { background-color: #2b2b2b; color: #ff5555; font-weight: bold; "
                "padding: 6px 14px; border: 1px solid #ff5555; border-radius: 4px; }"
            )

    def poll_ndi_sources(self):
        ndi.find_wait_for_sources(self.find_instance, 500)
        sources = ndi.find_get_current_sources(self.find_instance)
        names = [s.p_ndi_name for s in sources]
        for tile in self.tiles:
            tile.update_source_list(names)
        self.statusBar().showMessage(
            f"Discovered {len(names)} NDI source(s) | Encoder: {self.detected_encoder}", 4000
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
                tile = VideoTile(tile_id, detected_encoder=self.detected_encoder)
                tile.tile_focused.connect(self.handle_tile_focus)
                self.grid_layout.addWidget(tile, r, c)
                self.tiles.append(tile)
                tile_id += 1

        self.poll_ndi_sources()

    @Slot(int)
    def handle_tile_focus(self, clicked_id: int):
        self.focused_tile_id = clicked_id
        for tile in self.tiles:
            tile.set_focus_state(tile.tile_id == clicked_id)

    def toggle_master_record(self, checked: bool):
        active_tiles = [t for t in self.tiles if t.is_assigned()]
        if not active_tiles:
            self.statusBar().showMessage("Assign at least one NDI source before Master Recording.", 3000)
            self.btn_master_rec.setChecked(False)
            return

        if checked:
            session_tag = f"Take_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            count = sum(1 for t in active_tiles if t.start_tile_recording(session_tag=session_tag))
            self._style_master_button(True)
            self.statusBar().showMessage(f"Master Recording active across {count} stream(s) ({session_tag})")
        else:
            for tile in self.tiles:
                tile.stop_tile_recording()
            self._style_master_button(False)
            self.statusBar().showMessage("Master Recording stopped. Files saved to ./captures/", 5000)

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