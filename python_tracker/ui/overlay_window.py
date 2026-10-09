"""
Multi-Gun Projector Overlay Host Window.
Renders independent configurable overlay layers for each Gun ID on the same selected projector.
Supports Individual Tracking Mode and Diagnostic Mode with distinct visual styling per gun.
"""

import time
from typing import List, Optional

from PySide6.QtCore import Qt, QRect, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPen, QColor, QFont, QBrush
from PySide6.QtWidgets import QWidget

from core import config
from core.gun_state import MultiGunManager, GunState
from network.udp_client import UDPClient
from .display_manager import DisplayInfo


class ProjectorOverlay(QWidget):
    """
    Dedicated borderless fullscreen overlay host for the selected secondary display / projector.
    Renders simultaneous, independent visual layers for all registered Gun IDs.
    All layers share the exact same viewport coordinate space.
    """

    closed = Signal()
    active_gun_changed = Signal(int)

    def __init__(
        self,
        display_info: DisplayInfo,
        gun_manager: MultiGunManager,
        udp_client: Optional[UDPClient] = None,
        active_gun_id: int = 1,
        transparent: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.display_info: DisplayInfo = display_info
        self.gun_manager: MultiGunManager = gun_manager
        self.udp_client: Optional[UDPClient] = udp_client
        self.active_gun_id: int = active_gun_id
        self.full_transparent: bool = transparent

        # Overlay Modes: False = Individual Tracking Mode (Mode A), True = Diagnostic Mode (Mode B)
        self.diagnostic_mode: bool = False
        self.show_hud: bool = True

        # True only after the user explicitly selects a gun from the dashboard table.
        # Until then, mouse movement does NOT move any crosshair.
        self.gun_selected: bool = False

        self.last_frame_time: float = time.time()
        self.current_fps: float = float(config.OVERLAY_FPS)

        self._configure_window()
        self._setup_connections()

        # 60 FPS render & polling loop
        self.render_timer = QTimer(self)
        self.render_timer.setInterval(int(1000 / config.OVERLAY_FPS))
        self.render_timer.timeout.connect(self._on_frame_tick)
        self.render_timer.start()

    def _configure_window(self):
        """Sets window properties for borderless fullscreen transparent overlay on target display."""
        if self.display_info.screen_obj is not None:
            self.setScreen(self.display_info.screen_obj)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        if self.display_info.screen_obj is not None:
            self.setGeometry(self.display_info.screen_obj.geometry())
        else:
            self.setGeometry(
                self.display_info.x,
                self.display_info.y,
                self.display_info.width,
                self.display_info.height,
            )

    def _setup_connections(self):
        # Repaint whenever any gun state, aim, or shot changes
        self.gun_manager.all_guns_changed.connect(self._safe_update)
        self.gun_manager.gun_state_changed.connect(self._on_gun_state_changed)
        self.gun_manager.gun_shot_fired.connect(self._on_gun_shot_fired)

    def _safe_update(self):
        try:
            if self.isVisible():
                self.update()
        except (RuntimeError, Exception):
            pass

    def _on_gun_state_changed(self, gun_id: int):
        self._safe_update()

    def _on_gun_shot_fired(self, gun_id: int, payload: dict):
        self._safe_update()

    def closeEvent(self, event):
        try:
            self.gun_manager.all_guns_changed.disconnect(self._safe_update)
            self.gun_manager.gun_state_changed.disconnect(self._on_gun_state_changed)
            self.gun_manager.gun_shot_fired.disconnect(self._on_gun_shot_fired)
        except Exception:
            pass
        if hasattr(self, "render_timer") and self.render_timer.isActive():
            self.render_timer.stop()
        self.closed.emit()
        super().closeEvent(event)

    def show_fullscreen_overlay(self):
        """Positions and raises the borderless window on the target screen."""
        if self.display_info.screen_obj is not None:
            self.setScreen(self.display_info.screen_obj)
            geom = self.display_info.screen_obj.geometry()
            self.setGeometry(geom)
        else:
            self.setGeometry(
                self.display_info.x,
                self.display_info.y,
                self.display_info.width,
                self.display_info.height,
            )
        self.show()
        win = self.windowHandle()
        if win is not None:
            if self.display_info.screen_obj is not None:
                win.setScreen(self.display_info.screen_obj)
            win.setGeometry(self.geometry())
        self.raise_()
        self.activateWindow()

    def set_active_gun(self, gun_id: int):
        """Sets which Gun ID is actively moved by mouse. Called from dashboard table selection."""
        self.active_gun_id = gun_id
        self.gun_selected = True
        self.update()

    def toggle_diagnostic_mode(self):
        """Toggles between Individual Tracking Mode and Diagnostic Mode."""
        self.diagnostic_mode = not self.diagnostic_mode
        self.update()

    def _on_frame_tick(self):
        now = time.time()
        dt = now - self.last_frame_time
        self.last_frame_time = now
        if dt > 0:
            fps_instant = 1.0 / dt
            self.current_fps = 0.9 * self.current_fps + 0.1 * fps_instant

        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        """Move the selected gun's crosshair on hover — only after a gun is selected from the table."""
        if not self.gun_selected:
            super().mouseMoveEvent(event)
            return
        pos = event.position() if hasattr(event, "position") else event.pos()
        x, y = round(pos.x()), round(pos.y())
        if 0 <= x < self.width() and 0 <= y < self.height():
            active_gun = self.gun_manager.get_gun(self.active_gun_id)
            if active_gun is not None and active_gun.connected and (x != active_gun.latest_x or y != active_gun.latest_y):
                active_gun.update_aim(x, y, self.width(), self.height())
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        """Right-Click: Toggles CrossLane for Gun 1 only. Left-click has no action."""
        if event.button() == Qt.MouseButton.RightButton:
            active_gun = self.gun_manager.get_gun(self.active_gun_id)
            if active_gun is not None and active_gun.gun_id == 1:
                self.gun_manager.set_gun_cross_lane(1, not active_gun.cross_lane)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        """All keyboard shortcuts removed; ignoring key events."""
        event.ignore()

    def paintEvent(self, event):
        w = self.width()
        h = self.height()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Background (drawn only if transparency is toggled off)
        if not self.full_transparent:
            painter.fillRect(0, 0, w, h, QColor("#080c10"))

        # 2. Subtle Outer Border (1px green to verify projector boundaries)
        pen_border = QPen(QColor("#00d26a"))
        pen_border.setWidth(2)
        painter.setPen(pen_border)
        painter.drawRect(1, 1, w - 2, h - 2)

        # 3. Hardware Optical Center Crosshair
        cx, cy = w // 2, h // 2
        pen_center = QPen(QColor(100, 110, 125, 140))
        pen_center.setStyle(Qt.PenStyle.DashLine)
        pen_center.setWidth(1)
        painter.setPen(pen_center)
        painter.drawLine(0, cy, w, cy)
        painter.drawLine(cx, 0, cx, h)

        # 4. Render Individual Gun Layers (Connected Guns Only)
        guns = self.gun_manager.get_connected_guns()
        banner_y_offset = 24

        for gun in guns:
            if not gun.overlay_visible:
                continue

            # Draw aim reticle if coordinates are valid
            if gun.has_valid_aim and gun.latest_x is not None and gun.latest_y is not None:
                self._draw_gun_marker(painter, gun)

            # Draw Shot Feedback Banner if fired recently (< 2.0s)
            now = time.time()
            if now - gun.last_shot_time < 2.0 and gun.last_shot_text:
                self._draw_shot_banner(painter, gun, banner_y_offset)
                banner_y_offset += 48

        # 5. Diagnostic Mode or Individual Tracking Mode HUD
        if self.show_hud:
            if self.diagnostic_mode:
                self._draw_diagnostic_hud(painter, guns)
            else:
                self._draw_tracking_hud(painter)

        # 6. Bottom Status Bar
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor(139, 148, 158, 200))
        mode_str = "DIAGNOSTIC" if self.diagnostic_mode else "TRACKING"
        if self.gun_selected:
            hint = f"Active: Gun {self.active_gun_id}  |  [Right-Click] Toggle CrossLane"
        else:
            hint = "Select a gun from the dashboard table to aim"
        painter.drawText(45, self.height() - 25, f"[Mode: {mode_str}]  |  {hint}")

        painter.end()

    def _draw_gun_marker(self, painter: QPainter, gun: GunState):
        """Draws an individual reticle layer tailored specifically for this Gun ID."""
        if gun.latest_x is None or gun.latest_y is None:
            return

        px: int = gun.latest_x
        py: int = gun.latest_y
        color = QColor(gun.color)
        is_active = (gun.gun_id == self.active_gun_id)

        # Outer Reticle Ring
        ring_pen = QPen(color, 3 if is_active else 2)
        painter.setPen(ring_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(px - 20, py - 20, 40, 40)

        # Active Gun Double Ring Glow
        if is_active:
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 100), 1, Qt.PenStyle.DashLine))
            painter.drawEllipse(px - 28, py - 28, 56, 56)

        # Center Dot
        painter.setPen(Qt.PenStyle.NoPen)
        center_dot_color = QColor("#ffffff") if is_active else color
        painter.setBrush(QBrush(center_dot_color))
        painter.drawEllipse(px - 3, py - 3, 6, 6)

        # Crosshair Ticks
        painter.setPen(QPen(color, 2))
        painter.drawLine(px - 28, py, px - 12, py)
        painter.drawLine(px + 12, py, px + 28, py)
        painter.drawLine(px, py - 28, px, py - 12)
        painter.drawLine(px, py + 12, px, py + 28)

        # Gun Badge Label (e.g. "[G1] (1024, 768) L:1")
        badge_rect = QRect(px + 16, py + 16, 170, 42)
        painter.setPen(QPen(QColor("#30363d"), 1))
        painter.setBrush(QBrush(QColor(16, 20, 26, 220)))
        painter.drawRoundedRect(badge_rect, 6, 6)

        # Gun Tag
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(color)
        gun_tag = f"GUN {gun.gun_id}" + (" [ACTIVE]" if is_active else "")
        painter.drawText(px + 22, py + 32, gun_tag)

        # Connection status indicator dot
        dot_color = QColor("#3fb950") if gun.connected else QColor("#da3633")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(dot_color))
        painter.drawEllipse(px + 168, py + 24, 8, 8)

        # Coordinates & CrossLane Tag
        painter.setFont(QFont("Consolas", 8))
        painter.setPen(QColor("#c9d1d9"))
        cl_tag = " [CL]" if (gun.cross_lane and gun.gun_id == 1) else ""
        coord_txt = f"({px}, {py}){cl_tag}"
        painter.drawText(px + 22, py + 50, coord_txt)

    def _draw_shot_banner(self, painter: QPainter, gun: GunState, y_pos: int):
        """Displays visual confirmation banner when a gun fires."""
        banner_w = 340
        banner_h = 38
        banner_rect = QRect(24, y_pos, banner_w, banner_h)

        color = QColor(gun.color)
        painter.setPen(QPen(color, 1))
        painter.setBrush(QBrush(QColor(16, 45, 26, 230)))
        painter.drawRoundedRect(banner_rect, 6, 6)

        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.setPen(color)
        painter.drawText(banner_rect, Qt.AlignmentFlag.AlignCenter, gun.last_shot_text)

    def _draw_tracking_hud(self, painter: QPainter):
        """Mode A: Renders compact telemetry HUD for the active controller."""
        active_gun = self.gun_manager.get_gun(self.active_gun_id)
        if not active_gun:
            return

        hud_w = 250
        hud_h = 170
        hud_rect = QRect(self.width() - hud_w - 24, 24, hud_w, hud_h)

        painter.setPen(QPen(QColor("#30363d"), 1))
        painter.setBrush(QBrush(QColor(16, 20, 26, 220)))
        painter.drawRoundedRect(hud_rect, 8, 8)

        color = QColor(active_gun.color)
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        painter.setPen(color)
        painter.drawText(hud_rect.x() + 14, hud_rect.y() + 26, f"GUN {active_gun.gun_id} TELEMETRY")

        painter.setPen(QPen(QColor("#30363d"), 1))
        painter.drawLine(hud_rect.x() + 14, hud_rect.y() + 34, hud_rect.x() + hud_w - 14, hud_rect.y() + 34)

        if active_gun.gun_id == 1:
            cl_text = "ON" if active_gun.cross_lane else "OFF"
            cl_color = color if active_gun.cross_lane else QColor("#8b949e")
        else:
            cl_text = "N/A (Gun 1 Only)"
            cl_color = QColor("#8b949e")

        rows = [
            ("Status:", "CONNECTED" if active_gun.connected else "DISCONNECTED", QColor("#3fb950") if active_gun.connected else QColor("#da3633")),
            ("Aim X:", f"{active_gun.latest_x} px" if active_gun.has_valid_aim else "No Aim", QColor("#58a6ff")),
            ("Aim Y:", f"{active_gun.latest_y} px" if active_gun.has_valid_aim else "No Aim", QColor("#58a6ff")),
            ("Lane:", f"{active_gun.lane}", QColor("#f0f6fc")),
            ("CrossLane:", cl_text, cl_color),
            ("Shots:", f"{active_gun.shot_count}", QColor("#f0f6fc")),
        ]

        painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        y = hud_rect.y() + 52
        for label, val, c in rows:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(hud_rect.x() + 14, y, label)
            painter.setPen(c)
            painter.drawText(hud_rect.x() + 110, y, val)
            y += 18

    def _draw_diagnostic_hud(self, painter: QPainter, guns: List[GunState]):
        """Mode B: Diagnostic Mode status panel displaying all connected guns simultaneously."""
        card_w = 460
        card_h = 44 + max(1, len(guns)) * 26
        hud_rect = QRect(self.width() - card_w - 24, 24, card_w, card_h)

        painter.setPen(QPen(QColor("#388bfd"), 2))
        painter.setBrush(QBrush(QColor(13, 17, 23, 235)))
        painter.drawRoundedRect(hud_rect, 8, 8)

        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        painter.setPen(QColor("#58a6ff"))
        painter.drawText(hud_rect.x() + 14, hud_rect.y() + 26, "DIAGNOSTIC STATUS — CONNECTED GUNS")

        painter.setPen(QPen(QColor("#30363d"), 1))
        painter.drawLine(hud_rect.x() + 14, hud_rect.y() + 34, hud_rect.x() + card_w - 14, hud_rect.y() + 34)

        y = hud_rect.y() + 56
        painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))

        if not guns:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(hud_rect.x() + 14, y, "No guns currently connected.")
            return

        for gun in guns:
            color = QColor(gun.color)

            # Dot
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(hud_rect.x() + 14, y - 9, 8, 8)

            # Label
            tag = f"G{gun.gun_id}" + ("*" if gun.gun_id == self.active_gun_id else " ")
            painter.setPen(color)
            painter.drawText(hud_rect.x() + 28, y, tag)

            # Port & Connection
            status_txt = "CONN" if gun.connected else "DISC"
            status_col = QColor("#3fb950") if gun.connected else QColor("#da3633")
            painter.setPen(status_col)
            port_label = gun.port or "NO-PORT"
            painter.drawText(hud_rect.x() + 75, y, f"{port_label} [{status_txt}]")

            # Coordinates
            coords = f"({gun.latest_x},{gun.latest_y})" if gun.has_valid_aim else "(No Aim)"
            painter.setPen(QColor("#58a6ff"))
            painter.drawText(hud_rect.x() + 225, y, coords)

            # Trigger state
            trig_txt = "TRIG" if gun.trigger_state == 1 else "IDLE"
            trig_col = QColor("#ff7b72") if gun.trigger_state == 1 else QColor("#8b949e")
            painter.setPen(trig_col)
            painter.drawText(hud_rect.x() + 355, y, trig_txt)

            # Shots
            painter.setPen(QColor("#f0f6fc"))
            painter.drawText(hud_rect.x() + 410, y, f"#{gun.shot_count}")

            y += 26
