import sys
from datetime import datetime
import NDIlib as ndi
from PySide6.QtCore import Slot
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QGridLayout, QToolBar, QLabel, QSpinBox, QPushButton,
    QSizePolicy, QDialog
)
from ndi_worker import detect_best_h264_encoder, get_ndi_name
from ui_components import (
    VideoTile, SlotSettingsDialog, create_carbon_fiber_pixmap
)


class CarbonFiberContainer(QWidget):
    """Paints a procedural carbon fiber texture around and between all grid tiles."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.carbon_tile = create_carbon_fiber_pixmap()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawTiledPixmap(self.rect(), self.carbon_tile)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NDI MultiView & ISO Recorder")

        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.availableGeometry())

        if not ndi.initialize():
            raise SystemError("Could not initialize NDI SDK. Verify NDI Runtime is installed.")
        self.find_instance = ndi.find_create_v2()

        self.detected_encoder = detect_best_h264_encoder()
        self.statusBar().showMessage(
            f"Encoder: {self.detected_encoder} | Click empty tab area to select (Yellow) | Double-click to deselect all"
        )

        self.tiles: list[VideoTile] = []
        self.tile_configs = {}

        self._setup_toolbar()

        # Central widget with Carbon Fiber background
        central = CarbonFiberContainer()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        # Outer margin around tabs so the carbon fiber surround is clearly visible
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        self.grid_container = QWidget()
        self.grid_container.setAttribute(
            sys.modules["PySide6.QtCore"].Qt.WA_TranslucentBackground, True
        )
        self.grid_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        # Spacing between tabs showing the carbon fiber weave
        self.grid_layout.setSpacing(12)
        main_layout.addWidget(self.grid_container, stretch=1)

        self.rebuild_grid()

    def _setup_toolbar(self):
        toolbar = QToolBar("Controls")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel("  Grid Rows: "))
        self.row_spin = QSpinBox()
        self.row_spin.setRange(1, 6)
        self.row_spin.setValue(2)
        toolbar.addWidget(self.row_spin)

        toolbar.addWidget(QLabel("  Cols: "))
        self.col_spin = QSpinBox()
        self.col_spin.setRange(1, 6)
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

        self.btn_master_pause = QPushButton("Pause All")
        self.btn_master_pause.setCheckable(True)
        self.btn_master_pause.setEnabled(False)
        self.btn_master_pause.clicked.connect(self.toggle_master_pause)
        toolbar.addWidget(self.btn_master_pause)

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
        names = [get_ndi_name(s) for s in sources]
        for tile in self.tiles:
            tile.update_source_list(names)
        self.statusBar().showMessage(
            f"Discovered {len(names)} NDI source(s) | Encoder: {self.detected_encoder}", 4000
        )

    def rebuild_grid(self):
        saved_sources = {}
        for tile in self.tiles:
            self.tile_configs[tile.tile_id] = tile.get_config()
            saved_sources[tile.tile_id] = tile.source_selector.currentText()
            tile.cleanup()
            self.grid_layout.removeWidget(tile)
            tile.deleteLater()
        self.tiles.clear()

        for i in range(10):
            self.grid_layout.setRowStretch(i, 0)
            self.grid_layout.setColumnStretch(i, 0)

        rows = self.row_spin.value()
        cols = self.col_spin.value()

        for r in range(rows):
            self.grid_layout.setRowStretch(r, 1)
        for c in range(cols):
            self.grid_layout.setColumnStretch(c, 1)

        tile_id = 0
        for r in range(rows):
            for c in range(cols):
                cfg = self.tile_configs.get(tile_id)
                tile = VideoTile(tile_id, detected_encoder=self.detected_encoder, initial_config=cfg)
                tile.tile_clicked.connect(self.handle_tile_click)
                tile.tile_double_clicked.connect(self.deselect_all_tiles)
                tile.settings_requested.connect(self.open_slot_settings)
                self.grid_layout.addWidget(tile, r, c)
                self.tiles.append(tile)
                tile_id += 1

        self.poll_ndi_sources()

        for tile in self.tiles:
            prev_src = saved_sources.get(tile.tile_id)
            if prev_src and prev_src != "--- Select Source ---":
                idx = tile.source_selector.findText(prev_src)
                if idx >= 0:
                    tile.source_selector.setCurrentIndex(idx)
                    tile.on_source_selected()

    @Slot(int)
    def handle_tile_click(self, clicked_id: int):
        """Toggles selection (yellow border) on the clicked tab so multiple tabs can be selected."""
        for tile in self.tiles:
            if tile.tile_id == clicked_id:
                tile.set_selected_state(not tile.is_selected)
                break
        selected_count = sum(1 for t in self.tiles if t.is_selected)
        self.statusBar().showMessage(
            f"{selected_count} tab(s) selected. Double-click any tab to deselect all.", 3000
        )

    @Slot()
    def deselect_all_tiles(self):
        """Double-clicking any tab deselects all tabs."""
        for tile in self.tiles:
            tile.set_selected_state(False)
        self.statusBar().showMessage("All tabs deselected.", 2500)

    @Slot(int)
    def open_slot_settings(self, clicked_tile_id: int):
        """
        Opens Slot Settings for all currently selected tabs if a selected tab's gear icon is clicked,
        or for the individual tab if it was not selected.
        """
        selected_tiles = [t for t in self.tiles if t.is_selected]
        clicked_tile = next((t for t in self.tiles if t.tile_id == clicked_tile_id), None)
        if not clicked_tile:
            return

        if clicked_tile in selected_tiles and len(selected_tiles) > 0:
            target_tiles = selected_tiles
        else:
            target_tiles = [clicked_tile]

        dlg = SlotSettingsDialog(target_tiles, parent=self)
        if dlg.exec() == QDialog.Accepted:
            res = dlg.get_settings_result()
            audio_action = res.get("audio_action", "mute")

            if audio_action == "exclusive":
                for t in self.tiles:
                    t.set_host_audio(False)

            for t in target_tiles:
                t.apply_batch_settings(res)
                if audio_action in ("exclusive", "mix"):
                    t.set_host_audio(True)
                else:
                    t.set_host_audio(False)

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
            self.btn_master_pause.setEnabled(True)
            self.btn_master_pause.setChecked(False)
            self.btn_master_pause.setText("Pause All")
            self.statusBar().showMessage(f"Master Recording active across {count} stream(s) ({session_tag})")
        else:
            for tile in self.tiles:
                tile.stop_tile_recording()
            self._style_master_button(False)
            self.btn_master_pause.setChecked(False)
            self.btn_master_pause.setEnabled(False)
            self.btn_master_pause.setText("Pause All")
            self.statusBar().showMessage("Master Recording stopped.", 5000)

    def toggle_master_pause(self, checked: bool):
        for tile in self.tiles:
            if tile.is_recording:
                tile.set_tile_paused(checked)
        self.btn_master_pause.setText("Resume All" if checked else "Pause All")
        self.statusBar().showMessage(
            "All recordings paused." if checked else "All recordings resumed.", 3000
        )

    def closeEvent(self, event):
        for tile in self.tiles:
            tile.cleanup()
        ndi.find_destroy(self.find_instance)
        ndi.destroy()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())
