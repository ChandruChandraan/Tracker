"""
Independent Per-Gun State Management for Multi-Gun Firing Simulator.
Maintains isolated state, coordinates, trigger status, and sequence tracking for each Gun ID.
"""

import time
from typing import Dict, Optional, List, Any
from PySide6.QtCore import QObject, Signal

from . import config


class GunState:
    """Represents the complete runtime state for a single simulator gun controller."""

    def __init__(self, gun_id: int, lane: Optional[int] = None):
        self.gun_id: int = gun_id
        self.port: Optional[str] = None
        self.connected: bool = False
        self.trigger_state: int = 0  # 0 = released, 1 = pressed
        self.latest_x: Optional[int] = None
        self.latest_y: Optional[int] = None
        self.has_valid_aim: bool = False
        self.lane: int = lane if lane is not None else gun_id
        self.cross_lane: bool = False
        self.last_heartbeat: float = 0.0
        self.last_trigger_seq: int = 0
        self.shot_count: int = 0
        self.overlay_visible: bool = True
        self.color: str = config.GUN_COLORS.get(self.gun_id, "#58a6ff")

        # Feedback banner state
        self.last_shot_time: float = 0.0
        self.last_shot_text: str = ""

    def update_aim(self, x: int, y: int, max_w: int = 3840, max_h: int = 2160):
        """Updates aim coordinates for this gun only, validating against viewport boundaries."""
        clamped_x = max(0, min(max_w - 1, x))
        clamped_y = max(0, min(max_h - 1, y))
        self.latest_x = clamped_x
        self.latest_y = clamped_y
        self.has_valid_aim = True

    def invalidate_aim(self):
        """Clears aim coordinates so invalid or stale aim is never sent or fabricated."""
        self.has_valid_aim = False
        self.latest_x = None
        self.latest_y = None

    def record_shot(self) -> int:
        """Increments shot round count and records feedback."""
        self.shot_count += 1
        self.last_shot_time = time.time()
        coords_str = f"({self.latest_x} px, {self.latest_y} px)" if self.has_valid_aim else "(No Aim)"
        self.last_shot_text = f"GUN {self.gun_id} SHOT #{self.shot_count} {coords_str}"
        return self.shot_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gun_id": self.gun_id,
            "port": self.port,
            "connected": self.connected,
            "trigger_state": self.trigger_state,
            "latest_x": self.latest_x,
            "latest_y": self.latest_y,
            "has_valid_aim": self.has_valid_aim,
            "lane": self.lane,
            "cross_lane": (self.cross_lane if self.gun_id == 1 else False),
            "last_heartbeat": self.last_heartbeat,
            "last_trigger_seq": self.last_trigger_seq,
            "shot_count": self.shot_count,
            "overlay_visible": self.overlay_visible,
            "color": self.color,
        }


class MultiGunManager(QObject):
    """
    Coordinates all independent GunState instances across serial input,
    projector overlay rendering, and Unreal Engine UDP output.
    """

    gun_registered = Signal(int)          # (gun_id)
    gun_state_changed = Signal(int)       # (gun_id)
    gun_shot_fired = Signal(int, dict)    # (gun_id, payload_dict)
    all_guns_changed = Signal()

    def __init__(self, default_gun_count: int = 0):
        super().__init__()
        self._guns: Dict[int, GunState] = {}
        self.display_width: int = 1920
        self.display_height: int = 1080
        self.display_index: int = 2
        self.display_x: int = 0
        self.display_y: int = 0

        # Initialize default guns (only if default_gun_count > 0, otherwise guns register dynamically on connect)
        for gid in range(1, default_gun_count + 1):
            gun = self.get_or_create_gun(gid)
            slot_x = int((self.display_width / (default_gun_count + 1)) * gid)
            slot_y = int(self.display_height / 2)
            gun.update_aim(slot_x, slot_y, self.display_width, self.display_height)

    def set_display_geometry(self, index: int, x: int, y: int, width: int, height: int):
        """Updates display viewport metadata for UDP packets and coordinate bounds."""
        old_w = self.display_width
        old_h = self.display_height
        self.display_index = index
        self.display_x = x
        self.display_y = y
        self.display_width = max(1, width)
        self.display_height = max(1, height)

        scale_x = self.display_width / max(1, old_w)
        scale_y = self.display_height / max(1, old_h)

        # Scale and re-clamp valid aim positions for each individual gun
        for gid, gun in self._guns.items():
            if gun.has_valid_aim and gun.latest_x is not None and gun.latest_y is not None:
                new_x = round(gun.latest_x * scale_x)
                new_y = round(gun.latest_y * scale_y)
                gun.update_aim(new_x, new_y, self.display_width, self.display_height)
            else:
                total = max(4, len(self._guns))
                slot_x = int((self.display_width / (total + 1)) * gid)
                slot_y = int(self.display_height / 2)
                gun.update_aim(slot_x, slot_y, self.display_width, self.display_height)
        self.all_guns_changed.emit()

    def get_or_create_gun(self, gun_id: int) -> GunState:
        """Retrieves an existing GunState or creates a new one safely."""
        if gun_id not in self._guns:
            gun = GunState(gun_id)
            total = max(4, gun_id)
            slot_x = int((self.display_width / (total + 1)) * gun_id)
            slot_y = int(self.display_height / 2)
            gun.update_aim(slot_x, slot_y, self.display_width, self.display_height)
            self._guns[gun_id] = gun
            self.gun_registered.emit(gun_id)
            self.all_guns_changed.emit()
            return gun
        return self._guns[gun_id]

    def get_gun(self, gun_id: int) -> Optional[GunState]:
        return self._guns.get(gun_id)

    def get_all_guns(self) -> List[GunState]:
        """Returns sorted list of all registered guns by Gun ID."""
        return [self._guns[gid] for gid in sorted(self._guns.keys())]

    def get_connected_guns(self) -> List[GunState]:
        """Returns sorted list of all currently connected guns."""
        return [self._guns[gid] for gid in sorted(self._guns.keys()) if self._guns[gid].connected]

    def remove_gun(self, gun_id: int):
        """Completely unregisters a gun from state tracking."""
        if gun_id in self._guns:
            del self._guns[gun_id]
            self.all_guns_changed.emit()

    def set_gun_aim(self, gun_id: int, x: int, y: int):
        """Updates coordinates strictly for the specified Gun ID."""
        gun = self.get_or_create_gun(gun_id)
        gun.update_aim(x, y, self.display_width, self.display_height)
        self.gun_state_changed.emit(gun_id)

    def set_gun_lane(self, gun_id: int, lane: int):
        gun = self.get_or_create_gun(gun_id)
        gun.lane = max(1, lane)
        self.gun_state_changed.emit(gun_id)

    def set_gun_cross_lane(self, gun_id: int, cross_lane: bool):
        gun = self.get_or_create_gun(gun_id)
        # Cross-lane is strictly restricted to Gun 1 only; all other guns are forced to False
        if gun_id == 1:
            gun.cross_lane = cross_lane
        else:
            gun.cross_lane = False
        self.gun_state_changed.emit(gun_id)

    def set_gun_overlay_visible(self, gun_id: int, visible: bool):
        gun = self.get_or_create_gun(gun_id)
        gun.overlay_visible = visible
        self.gun_state_changed.emit(gun_id)

    def set_all_overlays_visible(self, visible: bool):
        for gun in self._guns.values():
            gun.overlay_visible = visible
        self.all_guns_changed.emit()

    def handle_heartbeat(self, gun_id: int, port: Optional[str] = None):
        """Updates keepalive timestamp and connection state."""
        gun = self.get_or_create_gun(gun_id)
        gun.last_heartbeat = time.time()
        gun.connected = True
        if port is not None:
            gun.port = port
        self.gun_state_changed.emit(gun_id)

    def handle_trigger(self, gun_id: int, state: int, seq: int) -> Optional[Dict[str, Any]]:
        """
        Processes trigger event:
        - Rising edge only (state == 1) generates a shot.
        - Disregards exact duplicate or stale sequence numbers.
        - Detects ESP32 reboot (seq rollback) and resets counter so the gun
          immediately works again instead of silently dropping triggers.
        - Never generates shots while trigger remains held.
        - Uses ONLY this gun's latest aim coordinates (never shares or fabricates).
        Returns shot payload dictionary if a valid shot was fired, else None.
        """
        gun = self.get_or_create_gun(gun_id)
        gun.last_heartbeat = time.time()
        gun.connected = True

        if seq > 0:
            # Detect rollback: ESP32 rebooted and reset its counter.
            # If incoming seq is far below last seen, accept it as a fresh start.
            rollback = (gun.last_trigger_seq > 10) and (seq < gun.last_trigger_seq // 2)
            if rollback:
                gun.last_trigger_seq = 0  # reset so seq passes through

            # Drop exact duplicate / stale packets (not a rollback)
            if seq <= gun.last_trigger_seq:
                return None

        if seq > 0:
            gun.last_trigger_seq = seq

        prev_state = gun.trigger_state
        gun.trigger_state = 1 if state == 1 else 0

        # Check rising-edge transition
        is_rising_edge = (prev_state == 0 and gun.trigger_state == 1)

        self.gun_state_changed.emit(gun_id)

        if is_rising_edge:
            round_num = gun.record_shot()

            # Coordinates come from this gun
            shot_x = gun.latest_x if gun.latest_x is not None else (self.display_width // 2)
            shot_y = gun.latest_y if gun.latest_y is not None else (self.display_height // 2)

            payload = {
                "type": "shot",
                "gun": gun.gun_id,
                "lane": gun.lane,
                "x": shot_x,
                "y": shot_y,
                "cross_lane": (gun.cross_lane if gun.gun_id == 1 else False),
                "round": round_num,
                "display_width": self.display_width,
                "display_height": self.display_height,
            }
            self.gun_shot_fired.emit(gun_id, payload)
            return payload

        return None

    def check_heartbeat_timeouts(self, timeout_seconds: float = 3.0):
        """Marks controllers disconnected if no heartbeat has arrived within timeout."""
        now = time.time()
        changed = False
        for gun in self._guns.values():
            if gun.connected and (now - gun.last_heartbeat > timeout_seconds):
                gun.connected = False
                changed = True
                self.gun_state_changed.emit(gun.gun_id)
        if changed:
            self.all_guns_changed.emit()
