"""
Multi-Gun Firing Simulator Coordinator & Control Panel.
Coordinates USB Serial Microcontrollers, Multi-Gun Independent State,
Single Projector Multi-Layer Overlay, and Unreal Engine 5.8 UDP Telemetry.
"""

import sys
import time
import atexit
from pathlib import Path
from typing import Optional, List

# Ensure package directory is in sys.path regardless of execution CWD
_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QSlider,
    QSpinBox,
    QCheckBox,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTextEdit,
    QSplitter,
)

from core import config
from core.gun_state import MultiGunManager
from devices.serial_manager import SerialDeviceManager
from network.udp_client import UDPClient
from ui.display_manager import DisplayManager, DisplayInfo
from ui.overlay_window import ProjectorOverlay
from ui.unreal_process import UnrealManager


class ControlWindow(QMainWindow):
    """
    Main Multi-Gun Control Window for Firing Simulator.
    Manages display selection, multiple USB serial controllers, independent gun overlays,
    per-gun aim calibration, and Unreal Engine 5.8 UDP integration.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Firing Simulator — Multi-Gun Manager")
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)

        # Ensure Python Tracker widget always positions on Primary Monitor
        primary_screen = QApplication.primaryScreen()
        if primary_screen:
            self.setScreen(primary_screen)
            p_geom = primary_screen.geometry()
            self.move(p_geom.x() + 40, p_geom.y() + 40)

        # 1. Core State Managers
        self.gun_manager = MultiGunManager(default_gun_count=getattr(config, "SIMULATED_GUN_COUNT", 4))
        self.serial_manager = SerialDeviceManager(self.gun_manager)
        self.udp_client = UDPClient(config.UNREAL_IP, config.UNREAL_PORT)
        self.unreal_manager = UnrealManager(config.UNREAL_EXE_PATH)
        self._cleaned_up: bool = False
        atexit.register(self.cleanup_and_close_all)

        # 2. Display & Overlay State
        self.overlay_window: Optional[ProjectorOverlay] = None
        self.selected_display_index: Optional[int] = None
        self.current_displays: List[DisplayInfo] = []
        self.active_gun_id: int = 1

        # 3. Background Timers
        self.unreal_check_timer = QTimer(self)
        self.unreal_check_timer.setInterval(2000)
        self.unreal_check_timer.timeout.connect(self._check_unreal_status)

        # 4. Build UI and Connections
        self._init_ui()
        self._setup_connections()

        # 5. Start Background Workers
        self.udp_client.connect_client()
        self.serial_manager.start()
        self.refresh_displays()

        self.unreal_check_timer.start()

        # Auto-launch configurations
        if getattr(config, "AUTO_LAUNCH_UNREAL", True):
            self.launch_unreal()

        if getattr(config, "AUTO_START_OVERLAY", True):
            self.start_overlay()

    def _init_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        # Global Dark Theme Stylesheet
        self.setStyleSheet("""
            QMainWindow { background-color: #0d1117; }
            QWidget { background-color: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', Arial, sans-serif; font-size: 9.5pt; }
            QFrame.card { background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px; }
            QLabel.cardTitle { font-size: 10.5pt; font-weight: bold; color: #58a6ff; letter-spacing: 0.5px; }
            QPushButton {
                font-weight: bold; padding: 6px 12px; border-radius: 6px;
                border: 1px solid #30363d; background-color: #21262d; color: #c9d1d9;
            }
            QPushButton:hover { background-color: #30363d; border-color: #8b949e; }
            QPushButton#primaryBtn { background-color: #238636; color: #ffffff; border-color: #2ea043; }
            QPushButton#primaryBtn:hover { background-color: #2ea043; }
            QPushButton#dangerBtn { background-color: #da3633; color: #ffffff; border-color: #f85149; }
            QPushButton#dangerBtn:hover { background-color: #b62324; }
            QPushButton#accentBtn { background-color: #1f6feb; color: #ffffff; border-color: #388bfd; }
            QPushButton#accentBtn:hover { background-color: #388bfd; }
            QComboBox { background-color: #21262d; border: 1px solid #30363d; border-radius: 4px; padding: 4px 8px; color: #f0f6fc; }
            QSpinBox { background-color: #21262d; border: 1px solid #30363d; border-radius: 4px; padding: 4px 6px; color: #f0f6fc; font-family: Consolas; }
            QSlider::groove:horizontal { height: 6px; background: #30363d; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #58a6ff; border-radius: 3px; }
            QSlider::handle:horizontal { background: #f0f6fc; border: 1px solid #8b949e; width: 14px; margin: -4px 0; border-radius: 7px; }
            QTableWidget { background-color: #161b22; border: 1px solid #30363d; border-radius: 6px; gridline-color: #21262d; }
            QTableWidget::item { padding: 4px; }
            QHeaderView::section { background-color: #21262d; color: #8b949e; font-weight: bold; border: 1px solid #30363d; padding: 4px; }
            QTextEdit { background-color: #090d13; border: 1px solid #30363d; border-radius: 6px; font-family: Consolas, monospace; font-size: 8.5pt; color: #8b949e; }
        """)

        # --------------------------------------------------------------------
        # 1. Top Header Bar
        # --------------------------------------------------------------------
        header_layout = QHBoxLayout()
        header_title = QLabel("FIRING SIMULATOR — MULTI-GUN CONTROLLER")
        header_title.setStyleSheet("font-size: 13pt; font-weight: 800; color: #58a6ff; letter-spacing: 1px;")
        header_layout.addWidget(header_title)
        header_layout.addStretch()

        self.overlay_status_badge = QLabel("OVERLAY: OFF")
        self.overlay_status_badge.setStyleSheet("background-color: #21262d; color: #8b949e; font-weight: bold; padding: 4px 10px; border-radius: 4px;")
        header_layout.addWidget(self.overlay_status_badge)

        self.btn_toggle_overlay = QPushButton("Show Overlay")
        self.btn_toggle_overlay.setObjectName("primaryBtn")
        self.btn_toggle_overlay.clicked.connect(self._on_toggle_overlay_clicked)
        header_layout.addWidget(self.btn_toggle_overlay)

        self.unreal_status_badge = QLabel("UNREAL: CHECKING...")
        self.unreal_status_badge.setStyleSheet("background-color: #21262d; color: #8b949e; font-weight: bold; padding: 4px 10px; border-radius: 4px;")
        header_layout.addWidget(self.unreal_status_badge)

        self.btn_exit_all = QPushButton("Close All")
        self.btn_exit_all.setObjectName("dangerBtn")
        self.btn_exit_all.setStyleSheet("background-color: #da3633; color: #ffffff; font-weight: bold; padding: 4px 14px; border-radius: 4px;")
        self.btn_exit_all.setToolTip("Closes Tracker, Projector Overlay, and Unreal Engine all together")
        self.btn_exit_all.clicked.connect(self.close)
        header_layout.addWidget(self.btn_exit_all)

        main_layout.addLayout(header_layout)

        # --------------------------------------------------------------------
        # 2. Main Content Splitter (Left Column: Controls, Right Column: Grid & Logs)
        # --------------------------------------------------------------------
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left Column Widget: Active Gun Manual Aim Calibration
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        # Active Gun Manual Aim Calibration Card
        calib_card = QFrame()
        calib_card.setProperty("class", "card")
        c_layout = QVBoxLayout(calib_card)

        c_title = QLabel("ACTIVE GUN MANUAL AIM CALIBRATION")
        c_title.setProperty("class", "cardTitle")
        c_layout.addWidget(c_title)

        c_row1 = QHBoxLayout()
        c_row1.addWidget(QLabel("Select Active Gun:"))
        self.active_gun_combo = QComboBox()
        self.active_gun_combo.currentIndexChanged.connect(self._on_active_gun_combo_changed)
        c_row1.addWidget(self.active_gun_combo, 1)

        self.check_cross_lane = QCheckBox("Cross-Lane (Gun 1)")
        self.check_cross_lane.setToolTip("Toggle Cross-Lane (Available for Gun 1 only)")
        self.check_cross_lane.toggled.connect(self._on_cross_lane_toggled)
        c_row1.addWidget(self.check_cross_lane)

        c_row1.addWidget(QLabel("Lane:"))
        self.spin_lane = QSpinBox()
        self.spin_lane.setRange(1, 16)
        self.spin_lane.setValue(1)
        self.spin_lane.valueChanged.connect(self._on_lane_spin_changed)
        c_row1.addWidget(self.spin_lane)
        c_layout.addLayout(c_row1)

        # X Slider & Spin
        x_layout = QHBoxLayout()
        x_layout.addWidget(QLabel("Aim X:"))
        self.slider_x = QSlider(Qt.Orientation.Horizontal)
        self.slider_x.setRange(0, 1919)
        self.slider_x.valueChanged.connect(self._on_slider_x_changed)
        x_layout.addWidget(self.slider_x, 1)
        self.spin_x = QSpinBox()
        self.spin_x.setRange(0, 7680)
        self.spin_x.valueChanged.connect(self._on_spin_x_changed)
        x_layout.addWidget(self.spin_x)
        c_layout.addLayout(x_layout)

        # Y Slider & Spin
        y_layout = QHBoxLayout()
        y_layout.addWidget(QLabel("Aim Y:"))
        self.slider_y = QSlider(Qt.Orientation.Horizontal)
        self.slider_y.setRange(0, 1079)
        self.slider_y.valueChanged.connect(self._on_slider_y_changed)
        y_layout.addWidget(self.slider_y, 1)
        self.spin_y = QSpinBox()
        self.spin_y.setRange(0, 4320)
        self.spin_y.valueChanged.connect(self._on_spin_y_changed)
        y_layout.addWidget(self.spin_y)
        c_layout.addLayout(y_layout)

        # Action Buttons
        c_row_btn = QHBoxLayout()
        self.btn_manual_shot = QPushButton("Simulate Trigger Shot")
        self.btn_manual_shot.setObjectName("dangerBtn")
        self.btn_manual_shot.clicked.connect(self._manual_fire_active_shot)
        c_row_btn.addWidget(self.btn_manual_shot)
        c_layout.addLayout(c_row_btn)

        left_layout.addWidget(calib_card)
        left_layout.addStretch()

        splitter.addWidget(left_widget)

        # Right Column Widget
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        # Right Card A: Multi-Gun Table
        table_card = QFrame()
        table_card.setProperty("class", "card")
        t_layout = QVBoxLayout(table_card)

        t_header = QHBoxLayout()
        t_title = QLabel("CONNECTED GUN CONTROLLERS")
        t_title.setProperty("class", "cardTitle")
        t_header.addWidget(t_title)
        t_header.addStretch()

        self.lbl_connected_badge = QLabel("GUNS CONNECTED: 0")
        self.lbl_connected_badge.setStyleSheet(
            "background-color: #21262d; color: #8b949e; font-weight: bold; padding: 3px 8px; border-radius: 4px; font-size: 8.5pt;"
        )
        t_header.addWidget(self.lbl_connected_badge)
        t_layout.addLayout(t_header)

        self.gun_table = QTableWidget()
        self.gun_table.setColumnCount(8)
        self.gun_table.setHorizontalHeaderLabels([
            "Gun ID", "Port", "Status", "Trigger", "Aim Coordinates", "Shots", "Overlay", "Action"
        ])
        self.gun_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.gun_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t_layout.addWidget(self.gun_table)

        right_layout.addWidget(table_card, 2)

        # Right Card B: Live Activity & UDP Telemetry Log
        log_card = QFrame()
        log_card.setProperty("class", "card")
        l_layout = QVBoxLayout(log_card)

        l_header = QHBoxLayout()
        l_title = QLabel("SERIAL & UDP TELEMETRY LOG")
        l_title.setProperty("class", "cardTitle")
        l_header.addWidget(l_title)
        l_header.addStretch()

        btn_clear_log = QPushButton("Clear Log")
        btn_clear_log.clicked.connect(lambda: self.log_text.clear())
        l_header.addWidget(btn_clear_log)
        l_layout.addLayout(l_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        l_layout.addWidget(self.log_text)

        right_layout.addWidget(log_card, 1)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter)

    def _setup_connections(self):
        # Gun state updates
        self.gun_manager.all_guns_changed.connect(self._refresh_gun_table)
        self.gun_manager.gun_state_changed.connect(lambda gid: self._refresh_gun_table())
        self.gun_manager.gun_shot_fired.connect(self._on_gun_shot_fired)

        # Serial manager signals
        self.serial_manager.log_event.connect(self._append_log)
        self.serial_manager.device_connected.connect(lambda p, g: self._refresh_gun_table())
        self.serial_manager.device_disconnected.connect(lambda p, g: self._refresh_gun_table())

        # UDP signals
        self.udp_client.connection_status_changed.connect(self._on_udp_connection_changed)

        # Table selection signal
        self.gun_table.cellClicked.connect(self._on_table_cell_clicked)

        self._refresh_gun_table()

    # ------------------------------------------------------------------------
    # Display & Projector Management
    # ------------------------------------------------------------------------
    def refresh_displays(self):
        """Scans connected displays and automatically binds the projector/secondary display."""
        self.current_displays = DisplayManager.get_displays()
        if not self.current_displays:
            return

        default_to_primary = getattr(config, "DEFAULT_TO_PRIMARY_DISPLAY", False)
        selected_disp = self.current_displays[0]
        if not default_to_primary:
            secondary = next((d for d in self.current_displays if not d.is_primary), None)
            if secondary:
                selected_disp = secondary

        self.selected_display_index = selected_disp.index
        self.gun_manager.set_display_geometry(
            selected_disp.index, selected_disp.x, selected_disp.y, selected_disp.width, selected_disp.height
        )

        # Update slider maximums
        self.slider_x.blockSignals(True)
        self.slider_y.blockSignals(True)
        self.slider_x.setMaximum(max(0, selected_disp.width - 1))
        self.slider_y.setMaximum(max(0, selected_disp.height - 1))
        self.spin_x.setMaximum(selected_disp.width)
        self.spin_y.setMaximum(selected_disp.height)
        self.slider_x.blockSignals(False)
        self.slider_y.blockSignals(False)

        # Reposition overlay if running
        if self.overlay_window is not None:
            self.start_overlay()

        # Reposition Unreal if running
        self.unreal_manager.move_to_display(selected_disp)

    def _get_selected_display_info(self) -> Optional[DisplayInfo]:
        if not self.current_displays:
            return None
        if self.selected_display_index is not None:
            return next((d for d in self.current_displays if d.index == self.selected_display_index), self.current_displays[0])
        return self.current_displays[0]

    def _on_toggle_overlay_clicked(self):
        if self.overlay_window is not None:
            self.stop_overlay()
        else:
            self.start_overlay()

    def start_overlay(self):
        """Launches the multi-gun projector overlay on the selected display."""
        disp = self._get_selected_display_info()
        if not disp:
            return

        if self.overlay_window is not None:
            self.overlay_window.close()
            self.overlay_window = None

        self.overlay_window = ProjectorOverlay(
            display_info=disp,
            gun_manager=self.gun_manager,
            udp_client=self.udp_client,
            active_gun_id=self.active_gun_id,
            transparent=getattr(config, "OVERLAY_TRANSPARENT", True),
        )
        self.overlay_window.closed.connect(self._on_overlay_closed)
        self.overlay_window.active_gun_changed.connect(self.set_active_gun_id)
        self.overlay_window.show_fullscreen_overlay()

        self.overlay_status_badge.setText(f"OVERLAY: ON [{disp.index}]")
        self.overlay_status_badge.setStyleSheet("background-color: #123d24; color: #3fb950; font-weight: bold; padding: 4px 10px; border-radius: 4px;")
        self.btn_toggle_overlay.setText("Hide Overlay")

    def stop_overlay(self):
        overlay = self.overlay_window
        self.overlay_window = None
        if overlay is not None:
            try:
                overlay.hide()
                overlay.close()
                overlay.deleteLater()
            except Exception as e:
                print(f"[OVERLAY] Error closing overlay: {e}")

        if hasattr(self, "overlay_status_badge"):
            self.overlay_status_badge.setText("OVERLAY: OFF")
            self.overlay_status_badge.setStyleSheet("background-color: #21262d; color: #8b949e; font-weight: bold; padding: 4px 10px; border-radius: 4px;")
        if hasattr(self, "btn_toggle_overlay"):
            self.btn_toggle_overlay.setText("Show Overlay")

    def _on_overlay_closed(self):
        self.overlay_window = None
        self.overlay_status_badge.setText("OVERLAY: OFF")
        self.overlay_status_badge.setStyleSheet("background-color: #21262d; color: #8b949e; font-weight: bold; padding: 4px 10px; border-radius: 4px;")
        self.btn_toggle_overlay.setText("Show Overlay")

    # ------------------------------------------------------------------------
    # Unreal Process & UDP Handlers
    # ------------------------------------------------------------------------
    def launch_unreal(self):
        disp = self._get_selected_display_info()
        if disp:
            self.unreal_manager.launch(disp)
            self._check_unreal_status()

    def _check_unreal_status(self):
        running, pid = self.unreal_manager.is_running()
        if running:
            self.unreal_status_badge.setText(f"UNREAL: RUNNING (PID {pid})")
            self.unreal_status_badge.setStyleSheet("background-color: #123d24; color: #3fb950; font-weight: bold; padding: 4px 10px; border-radius: 4px;")
        else:
            self.unreal_status_badge.setText("UNREAL: NOT RUNNING")
            self.unreal_status_badge.setStyleSheet("background-color: #21262d; color: #8b949e; font-weight: bold; padding: 4px 10px; border-radius: 4px;")

    def _on_udp_connection_changed(self, connected: bool):
        pass

    def _on_gun_shot_fired(self, gun_id: int, payload: dict):
        """Sends shot UDP packet to Unreal Engine."""
        self.udp_client.send_shot(
            gun=payload["gun"],
            lane=payload["lane"],
            x=payload["x"],
            y=payload["y"],
            cross_lane=payload["cross_lane"],
            round_num=payload["round"],
            display_width=payload["display_width"],
            display_height=payload["display_height"],
        )
        self._append_log(f"[UDP SHOT] Gun {gun_id} -> Unreal (X={payload['x']}, Y={payload['y']}, Round={payload['round']})")

    # ------------------------------------------------------------------------
    # Active Gun Manual Controls & Sliders
    # ------------------------------------------------------------------------
    def set_active_gun_id(self, gid: int):
        """Switches the active gun across the dashboard, controls, and overlay."""
        self.active_gun_id = gid
        idx = self.active_gun_combo.findData(gid)
        if idx >= 0 and self.active_gun_combo.currentIndex() != idx:
            self.active_gun_combo.blockSignals(True)
            self.active_gun_combo.setCurrentIndex(idx)
            self.active_gun_combo.blockSignals(False)
        if self.overlay_window:
            self.overlay_window.set_active_gun(gid)
        self._sync_active_gun_controls()
        self._refresh_gun_table()

    def _set_active_controls_enabled(self, enabled: bool):
        self.spin_lane.setEnabled(enabled)
        self.slider_x.setEnabled(enabled)
        self.slider_y.setEnabled(enabled)
        self.spin_x.setEnabled(enabled)
        self.spin_y.setEnabled(enabled)
        self.btn_manual_shot.setEnabled(enabled)
        if not enabled:
            self.check_cross_lane.setEnabled(False)

    def _on_table_cell_clicked(self, row: int, col: int):
        guns = self.gun_manager.get_connected_guns()
        if 0 <= row < len(guns):
            self.set_active_gun_id(guns[row].gun_id)

    def _on_active_gun_combo_changed(self, index: int):
        if index < 0:
            return
        gid = self.active_gun_combo.currentData()
        if gid is not None:
            self.set_active_gun_id(int(gid))

    def _sync_active_gun_controls(self):
        guns = self.gun_manager.get_connected_guns()
        if not guns:
            self._set_active_controls_enabled(False)
            return

        gun = self.gun_manager.get_gun(self.active_gun_id)
        if not gun or not gun.connected:
            self.active_gun_id = guns[0].gun_id
            gun = guns[0]

        self._set_active_controls_enabled(True)
        self.spin_lane.blockSignals(True)
        self.spin_lane.setValue(gun.lane)
        self.spin_lane.blockSignals(False)

        self.check_cross_lane.blockSignals(True)
        if gun.gun_id == 1:
            self.check_cross_lane.setEnabled(True)
            self.check_cross_lane.setChecked(gun.cross_lane)
            self.check_cross_lane.setToolTip("Toggle Cross-Lane (Available for Gun 1 only)")
        else:
            self.check_cross_lane.setChecked(False)
            self.check_cross_lane.setEnabled(False)
            self.check_cross_lane.setToolTip("Cross-Lane is restricted to Gun 1 only")
        self.check_cross_lane.blockSignals(False)

        x = gun.latest_x if gun.latest_x is not None else (self.gun_manager.display_width // 2)
        y = gun.latest_y if gun.latest_y is not None else (self.gun_manager.display_height // 2)

        self.slider_x.blockSignals(True)
        self.slider_y.blockSignals(True)
        self.spin_x.blockSignals(True)
        self.spin_y.blockSignals(True)

        self.slider_x.setValue(x)
        self.slider_y.setValue(y)
        self.spin_x.setValue(x)
        self.spin_y.setValue(y)

        self.slider_x.blockSignals(False)
        self.slider_y.blockSignals(False)
        self.spin_x.blockSignals(False)
        self.spin_y.blockSignals(False)

    def _on_slider_x_changed(self, val: int):
        self.spin_x.blockSignals(True)
        self.spin_x.setValue(val)
        self.spin_x.blockSignals(False)
        self._apply_manual_aim()

    def _on_spin_x_changed(self, val: int):
        self.slider_x.blockSignals(True)
        self.slider_x.setValue(val)
        self.slider_x.blockSignals(False)
        self._apply_manual_aim()

    def _on_slider_y_changed(self, val: int):
        self.spin_y.blockSignals(True)
        self.spin_y.setValue(val)
        self.spin_y.blockSignals(False)
        self._apply_manual_aim()

    def _on_spin_y_changed(self, val: int):
        self.slider_y.blockSignals(True)
        self.slider_y.setValue(val)
        self.slider_y.blockSignals(False)
        self._apply_manual_aim()

    def _apply_manual_aim(self):
        gun = self.gun_manager.get_gun(self.active_gun_id)
        if gun:
            x_val = self.spin_x.value()
            y_val = self.spin_y.value()
            gun.update_aim(x_val, y_val, self.gun_manager.display_width, self.gun_manager.display_height)

    def _on_cross_lane_toggled(self, checked: bool):
        if self.active_gun_id == 1:
            self.gun_manager.set_gun_cross_lane(1, checked)
            self._append_log(f"[CROSSLANE] Gun 1 Cross-Lane set to {'ON' if checked else 'OFF'}")
            self._refresh_gun_table()
        else:
            self.check_cross_lane.blockSignals(True)
            self.check_cross_lane.setChecked(False)
            self.check_cross_lane.setEnabled(False)
            self.check_cross_lane.blockSignals(False)

    def _on_lane_spin_changed(self, val: int):
        self.gun_manager.set_gun_lane(self.active_gun_id, val)

    def _manual_fire_active_shot(self):
        active_gun = self.gun_manager.get_gun(self.active_gun_id)
        if active_gun:
            if not active_gun.has_valid_aim:
                active_gun.update_aim(self.spin_x.value(), self.spin_y.value(), self.gun_manager.display_width, self.gun_manager.display_height)
            seq = active_gun.last_trigger_seq + 1
            self.gun_manager.handle_trigger(active_gun.gun_id, state=1, seq=seq)
            self.gun_manager.handle_trigger(active_gun.gun_id, state=0, seq=seq + 1)

    # ------------------------------------------------------------------------
    # Table & Log Viewers
    # ------------------------------------------------------------------------
    def _refresh_gun_table(self):
        guns = self.gun_manager.get_connected_guns()
        count = len(guns)

        # Update connected count badge
        if hasattr(self, "lbl_connected_badge"):
            if count > 0:
                self.lbl_connected_badge.setText(f"GUNS CONNECTED: {count}")
                self.lbl_connected_badge.setStyleSheet(
                    "background-color: #123d24; color: #3fb950; font-weight: bold; padding: 3px 8px; border-radius: 4px; font-size: 8.5pt;"
                )
            else:
                self.lbl_connected_badge.setText("GUNS CONNECTED: 0")
                self.lbl_connected_badge.setStyleSheet(
                    "background-color: #21262d; color: #8b949e; font-weight: bold; padding: 3px 8px; border-radius: 4px; font-size: 8.5pt;"
                )

        # Update active gun dropdown to list only connected guns
        connected_ids = [g.gun_id for g in guns]
        if self.active_gun_id not in connected_ids:
            self.active_gun_id = connected_ids[0] if connected_ids else 1
            if self.overlay_window:
                self.overlay_window.set_active_gun(self.active_gun_id)

        self.active_gun_combo.blockSignals(True)
        self.active_gun_combo.clear()
        for g in guns:
            self.active_gun_combo.addItem(f"Gun {g.gun_id} ({g.color})", g.gun_id)
        idx = self.active_gun_combo.findData(self.active_gun_id)
        if idx >= 0:
            self.active_gun_combo.setCurrentIndex(idx)
        self.active_gun_combo.blockSignals(False)

        self._sync_active_gun_controls()

        if count == 0:
            self.gun_table.clearSpans()
            self.gun_table.setRowCount(1)
            msg_item = QTableWidgetItem("No guns currently connected. Power on a gun controller or click 'Enable 4 Virtual Guns'.")
            msg_item.setForeground(QColor("#8b949e"))
            msg_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gun_table.setItem(0, 0, msg_item)
            for col in range(1, 8):
                self.gun_table.setItem(0, col, QTableWidgetItem(""))
                self.gun_table.removeCellWidget(0, col)
            self.gun_table.setSpan(0, 0, 1, 8)
            return

        self.gun_table.clearSpans()
        self.gun_table.setRowCount(count)
        for row, gun in enumerate(guns):
            # Gun ID & Lane
            active_marker = " ★ ACTIVE" if gun.gun_id == self.active_gun_id else ""
            cl_marker = " [CL]" if (gun.gun_id == 1 and gun.cross_lane) else ""
            item_id = QTableWidgetItem(f"Gun {gun.gun_id} [Lane {gun.lane}]{active_marker}{cl_marker}")
            item_id.setForeground(QColor(gun.color))
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gun_table.setItem(row, 0, item_id)

            # Port
            item_port = QTableWidgetItem(gun.port or "None")
            item_port.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gun_table.setItem(row, 1, item_port)

            # Status
            status_str = "CONNECTED" if gun.connected else "DISCONNECTED"
            item_status = QTableWidgetItem(status_str)
            item_status.setForeground(QColor("#3fb950") if gun.connected else QColor("#da3633"))
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gun_table.setItem(row, 2, item_status)

            # Trigger
            trig_str = "PULLED" if gun.trigger_state == 1 else "RELEASED"
            item_trig = QTableWidgetItem(trig_str)
            item_trig.setForeground(QColor("#ff7b72") if gun.trigger_state == 1 else QColor("#8b949e"))
            item_trig.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gun_table.setItem(row, 3, item_trig)

            # Aim
            coords_str = f"({gun.latest_x}, {gun.latest_y})" if gun.has_valid_aim else "No Aim"
            item_coords = QTableWidgetItem(coords_str)
            item_coords.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gun_table.setItem(row, 4, item_coords)

            # Shots
            item_shots = QTableWidgetItem(str(gun.shot_count))
            item_shots.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gun_table.setItem(row, 5, item_shots)

            # Overlay Checkbox widget
            cb_overlay = QCheckBox()
            cb_overlay.setChecked(gun.overlay_visible)
            cb_overlay.toggled.connect(lambda chk, gid=gun.gun_id: self.gun_manager.set_gun_overlay_visible(gid, chk))
            container_cb = QWidget()
            cb_layout = QHBoxLayout(container_cb)
            cb_layout.addWidget(cb_overlay)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.gun_table.setCellWidget(row, 6, container_cb)

            # Action: Fire Test Button
            btn_fire = QPushButton("FIRE")
            btn_fire.setStyleSheet("background-color: #238636; color: #ffffff; padding: 2px 6px; font-size: 8.5pt;")
            btn_fire.clicked.connect(lambda _, gid=gun.gun_id: self._on_table_fire_clicked(gid))
            self.gun_table.setCellWidget(row, 7, btn_fire)

    def _on_table_fire_clicked(self, gun_id: int):
        gun = self.gun_manager.get_gun(gun_id)
        if gun:
            if not gun.has_valid_aim:
                gun.update_aim(self.gun_manager.display_width // 2, self.gun_manager.display_height // 2, self.gun_manager.display_width, self.gun_manager.display_height)
            seq = gun.last_trigger_seq + 1
            self.gun_manager.handle_trigger(gun_id, state=1, seq=seq)
            self.gun_manager.handle_trigger(gun_id, state=0, seq=seq + 1)

    def _append_log(self, text: str):
        t_str = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{t_str}] {text}")

    # ------------------------------------------------------------------------
    # Window Events & Cleanup
    # ------------------------------------------------------------------------
    def keyPressEvent(self, event):
        event.ignore()

    def cleanup_and_close_all(self):
        """Cleanly releases all serial connections, timers, UDP sockets, and closes overlay and Unreal."""
        if getattr(self, "_cleaned_up", False):
            return
        self._cleaned_up = True

        print("[SHUTDOWN] Closing Tracker, Overlay, and Unreal Engine...")

        # 1. Close projector overlay window immediately
        try:
            self.stop_overlay()
        except Exception as e:
            print(f"[SHUTDOWN] Error stopping overlay: {e}")

        # 2. Terminate Unreal Engine processes immediately
        try:
            self.unreal_manager.terminate()
        except Exception as e:
            print(f"[SHUTDOWN] Error terminating Unreal: {e}")

        # 3. Stop background periodic timers
        try:
            if hasattr(self, "unreal_check_timer") and self.unreal_check_timer.isActive():
                self.unreal_check_timer.stop()
        except Exception:
            pass

        # 4. Stop serial worker threads and simulation
        try:
            self.serial_manager.stop()
        except Exception as e:
            print(f"[SHUTDOWN] Error stopping serial manager: {e}")

        # 5. Disconnect UDP client socket
        try:
            self.udp_client.disconnect_client()
        except Exception as e:
            print(f"[SHUTDOWN] Error disconnecting UDP client: {e}")

        print("[SHUTDOWN] Cleanup finished successfully.")

    def closeEvent(self, event):
        """Cleanly releases all serial connections, timers, UDP sockets, overlay, and Unreal."""
        self.cleanup_and_close_all()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    window = ControlWindow()
    primary_screen = app.primaryScreen()
    if primary_screen:
        window.setScreen(primary_screen)
        geom = primary_screen.geometry()
        window.move(geom.x() + 40, geom.y() + 40)
    app.aboutToQuit.connect(window.cleanup_and_close_all)
    window.showMaximized()
    win_handle = window.windowHandle()
    if win_handle and primary_screen:
        win_handle.setScreen(primary_screen)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
