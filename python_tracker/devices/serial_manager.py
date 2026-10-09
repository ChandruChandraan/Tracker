"""
Non-blocking USB Serial Device Manager for Multi-Gun Firing Simulator.
Handles automatic port discovery, newline-delimited JSON parsing,
heartbeat tracking, auto-reconnection, and hardware simulation mode.
"""

import json
import threading
import time
from typing import Dict, Optional, List
import serial
import serial.tools.list_ports
from PySide6.QtCore import QObject, Signal, QTimer

from core import config
from core.gun_state import MultiGunManager


class SerialPortReaderThread(threading.Thread):
    """
    Dedicated worker thread for reading one serial COM port continuously.
    Buffers lines, validates JSON, and notifies the manager safely.
    """

    def __init__(self, port: str, baud_rate: int, callback, on_disconnect, manual_gun_id: Optional[int] = None):
        super().__init__(daemon=True)
        self.port: str = port
        self.baud_rate: int = baud_rate
        self.callback = callback
        self.on_disconnect = on_disconnect
        self.manual_gun_id: Optional[int] = manual_gun_id

        self._running: bool = False
        self._serial: Optional[serial.Serial] = None
        self.associated_gun_id: Optional[int] = manual_gun_id

    def run(self):
        self._running = True
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=0.2,
            )
            # ESP32-C3 USB CDC flow control
            try:
                self._serial.dtr = True
                self._serial.rts = True
            except Exception:
                pass
            # Flush buffers
            self._serial.reset_input_buffer()
        except Exception as e:
            self._running = False
            self.on_disconnect(self.port, self.associated_gun_id, str(e))
            return

        buffer = bytearray()
        while self._running:
            try:
                raw_chunk = self._serial.read(64)
                if raw_chunk:
                    buffer.extend(raw_chunk)
                    while b"\n" in buffer:
                        line_bytes, _, remaining = buffer.partition(b"\n")
                        buffer = bytearray(remaining)
                        line_str = line_bytes.decode("utf-8", errors="ignore").strip()
                        if line_str:
                            self._process_line(line_str)
                else:
                    time.sleep(0.01)
            except (serial.SerialException, OSError) as ex:
                break
            except Exception:
                time.sleep(0.01)

        self._running = False
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self.on_disconnect(self.port, self.associated_gun_id, "Port closed or unplugged")

    def _process_line(self, line: str):
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                return
            msg_type = data.get("type")
            gun_id = data.get("gun", self.manual_gun_id)

            if gun_id is not None:
                gun_id = int(gun_id)
                self.associated_gun_id = gun_id
            elif self.associated_gun_id is not None:
                # Trigger / heartbeat packets that omit the 'gun' field:
                # fall back to the ID negotiated by the hello packet on this port.
                gun_id = self.associated_gun_id

            self.callback(self.port, gun_id, msg_type, data, line)
        except json.JSONDecodeError:
            pass
        except Exception:
            pass

    def stop(self):
        self._running = False
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass


class SerialDeviceManager(QObject):
    """
    Manages all connected USB Serial gun controllers, auto-discovery,
    incoming JSON messages, and virtual simulated guns.
    """

    device_connected = Signal(str, int)          # (port, gun_id)
    device_disconnected = Signal(str, int)       # (port, gun_id)
    message_received = Signal(str, int, str, dict) # (port, gun_id, type, data)
    ports_scanned = Signal(list)                 # list of port name strings
    log_event = Signal(str)                      # log message string

    def __init__(self, gun_manager: MultiGunManager, parent=None):
        super().__init__(parent)
        self.gun_manager: MultiGunManager = gun_manager
        self.active_threads: Dict[str, SerialPortReaderThread] = {}
        self.port_to_gun: Dict[str, int] = {}
        self.gun_to_port: Dict[int, str] = {}
        self.auto_connect: bool = getattr(config, "AUTO_CONNECT_SERIAL", True)
        self._failed_ports: Dict[str, float] = {}
        self._logged_failures: set[str] = set()
        self._cooldown_seconds: float = 6.0

        # Background Watchdog & Scanner Timers
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(getattr(config, "SERIAL_AUTO_SCAN_INTERVAL_MS", 2000))
        self.scan_timer.timeout.connect(self.scan_ports)

        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.setInterval(1000)
        self.watchdog_timer.timeout.connect(self._on_watchdog_tick)

        # Simulation mode state
        self.is_simulated: bool = False
        self._sim_timer: Optional[QTimer] = None
        self._sim_seq: Dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}

    def start(self):
        """Starts background port scanning and watchdog timers."""
        self.scan_ports()
        self.scan_timer.start()
        self.watchdog_timer.start()

    def stop(self):
        """Stops all serial workers and timers cleanly."""
        try:
            self.scan_timer.stop()
        except Exception:
            pass
        try:
            self.watchdog_timer.stop()
        except Exception:
            pass
        try:
            self.disable_simulation()
        except Exception:
            pass
        try:
            self.disconnect_all()
        except Exception:
            pass

    def scan_ports(self) -> List[str]:
        """
        Discovers all available serial COM ports on the system.
        When auto_connect is True, automatically initiates connections to candidate
        USB microcontrollers to negotiate and bind Gun IDs from incoming JSON packets.
        """
        com_ports = serial.tools.list_ports.comports()
        all_port_names = [p.device for p in com_ports]
        self.ports_scanned.emit(all_port_names)

        # Detect active ports that physically disappeared from Windows (e.g. unplugged / powered off)
        current_port_set = set(all_port_names)
        for active_port in list(self.active_threads.keys()):
            if active_port not in current_port_set and not active_port.startswith("SIM-"):
                self.disconnect_port(active_port)

        if not self.auto_connect:
            return all_port_names

        now = time.time()
        for p in com_ports:
            port_name = p.device
            hwid = (p.hwid or "").upper()
            desc = (p.description or "").upper()

            # Skip motherboard/legacy ACPI or PCI ports to prevent blocking
            if hwid.startswith("ACPI") or hwid.startswith("PCI") or "COMMUNICATIONS PORT" in desc:
                continue

            # Skip if already actively connected
            if port_name in self.active_threads and self.active_threads[port_name].is_alive():
                continue

            # Respect error cooldown on ports that recently failed (e.g. Access Denied)
            last_fail = self._failed_ports.get(port_name, 0.0)
            if (now - last_fail) < self._cooldown_seconds:
                continue

            # Connect non-blocking reader to identify Gun ID
            self.connect_port(port_name)

        return all_port_names

    def connect_port(self, port: str, manual_gun_id: Optional[int] = None) -> bool:
        """Opens a serial port in a dedicated non-blocking thread."""
        if port in self.active_threads and self.active_threads[port].is_alive():
            return True

        self.log_event.emit(f"[SERIAL] Connecting to {port} (Baud: {config.SERIAL_BAUD_RATE})...")

        thread = SerialPortReaderThread(
            port=port,
            baud_rate=config.SERIAL_BAUD_RATE,
            callback=self._on_message_received,
            on_disconnect=self._on_port_disconnected,
            manual_gun_id=manual_gun_id,
        )
        self.active_threads[port] = thread
        thread.start()
        return True

    def disconnect_port(self, port: str):
        """Closes a specific serial port cleanly."""
        if port in self.active_threads:
            thread = self.active_threads.pop(port)
            thread.stop()
            gid = self.port_to_gun.pop(port, None)
            if gid is not None:
                self.gun_to_port.pop(gid, None)
                gun = self.gun_manager.get_gun(gid)
                if gun:
                    gun.connected = False
                    gun.port = None
                    self.gun_manager.gun_state_changed.emit(gid)
                    self.gun_manager.all_guns_changed.emit()
                self.device_disconnected.emit(port, gid)
            self.log_event.emit(f"[SERIAL] Disconnected {port}")

    def disconnect_all(self):
        """Closes all active serial ports."""
        ports = list(self.active_threads.keys())
        for p in ports:
            self.disconnect_port(p)

    def assign_port_to_gun(self, port: str, gun_id: int):
        """Manually binds a serial port to a specific Gun ID."""
        self.port_to_gun[port] = gun_id
        self.gun_to_port[gun_id] = port
        gun = self.gun_manager.get_or_create_gun(gun_id)
        gun.port = port
        gun.connected = True
        gun.last_heartbeat = time.time()
        self.gun_manager.gun_state_changed.emit(gun_id)
        self.gun_manager.all_guns_changed.emit()
        self.log_event.emit(f"[SERIAL] Manually assigned {port} -> Gun {gun_id}")

    def _on_message_received(self, port: str, gun_id: Optional[int], msg_type: str, data: dict, raw_line: str):
        """Handles parsed JSON packet received from serial thread."""
        # Port is healthy: clear failure tracking
        self._failed_ports.pop(port, None)
        self._logged_failures.discard(port)

        if gun_id is None:
            gun_id = self.port_to_gun.get(port)

        if gun_id is None:
            # Still no gun ID — log it so the user can see the drop
            if getattr(config, "DEBUG_LOGGING", True):
                self.log_event.emit(f"[SERIAL] {port} packet dropped — no gun ID yet (type={msg_type})")
            return

        # Clean up old port mapping if gun migrated from another COM port
        old_port = self.gun_to_port.get(gun_id)
        if old_port and old_port != port:
            self.port_to_gun.pop(old_port, None)
            if old_port in self.active_threads:
                self.disconnect_port(old_port)

        is_new_mapping = (self.port_to_gun.get(port) != gun_id) or (self.gun_to_port.get(gun_id) != port)
        gun = self.gun_manager.get_or_create_gun(gun_id)
        was_connected = gun.connected

        # Register bidirectional mapping
        self.port_to_gun[port] = gun_id
        self.gun_to_port[gun_id] = port
        gun.port = port
        gun.connected = True
        gun.last_heartbeat = time.time()

        if is_new_mapping or not was_connected:
            ver_info = f" (Firmware v{data.get('version')})" if msg_type == "hello" and "version" in data else ""
            msg = f"[SERIAL] Auto-assigned Gun {gun_id} on {port}{ver_info}"
            self.log_event.emit(msg)
            if getattr(config, "DEBUG_LOGGING", True):
                print(msg)
            self.device_connected.emit(port, gun_id)
            self.gun_manager.gun_state_changed.emit(gun_id)
            self.gun_manager.all_guns_changed.emit()

        if msg_type == "hello":
            self.gun_manager.handle_heartbeat(gun_id, port=port)

        elif msg_type == "heartbeat":
            self.gun_manager.handle_heartbeat(gun_id, port=port)

        elif msg_type == "trigger":
            state = int(data.get("state", 0))
            seq = int(data.get("seq", 0))
            trig_msg = f"[SERIAL] Gun {gun_id} Trigger: {'PRESSED' if state == 1 else 'RELEASED'} (seq={seq})"
            self.log_event.emit(trig_msg)
            if getattr(config, "DEBUG_LOGGING", True):
                print(trig_msg)
            result = self.gun_manager.handle_trigger(gun_id, state, seq)
            if result is None and state == 1:
                self.log_event.emit(f"[SERIAL] Gun {gun_id} trigger seq={seq} dropped (stale/duplicate)")

        self.message_received.emit(port, gun_id, msg_type, data)

    def _on_port_disconnected(self, port: str, gun_id: Optional[int], reason: str):
        """Callback when serial thread exits, port closes, or device is unplugged/powered off."""
        if port in self.active_threads:
            self.active_threads.pop(port, None)
        gid = self.port_to_gun.pop(port, gun_id)
        if gid is not None:
            self.gun_to_port.pop(gid, None)
            gun = self.gun_manager.get_gun(gid)
            if gun:
                gun.connected = False
                gun.port = None
                self.gun_manager.gun_state_changed.emit(gid)
                self.gun_manager.all_guns_changed.emit()
            self.device_disconnected.emit(port, gid)

        if "PermissionError" in reason or "Access is denied" in reason:
            self._failed_ports[port] = time.time()
            if port not in self._logged_failures:
                self._logged_failures.add(port)
                self.log_event.emit(f"[SERIAL] Access Denied on {port} (port in use by another app like Arduino IDE).")
        elif "Port closed or unplugged" in reason or "ClearCommError" in reason or "device does not recognize" in reason:
            # Clean disconnect / power off / unplug: clear failure cooldown so reconnect on power-on is immediate
            self._failed_ports.pop(port, None)
            self._logged_failures.discard(port)
            self.log_event.emit(f"[SERIAL] Gun {gid or ''} on {port} unplugged or powered off.")
        else:
            self._failed_ports.pop(port, None)
            self._logged_failures.discard(port)
            self.log_event.emit(f"[SERIAL] {port} disconnected: {reason}")

    def _on_watchdog_tick(self):
        """Checks for timed out heartbeats (> 3.0s with no packets received)."""
        now = time.time()
        timeout = getattr(config, "SERIAL_HEARTBEAT_TIMEOUT_S", 3.0)
        changed = False
        for gun in list(self.gun_manager.get_all_guns()):
            if gun.connected and (now - gun.last_heartbeat > timeout):
                gun.connected = False
                changed = True
                self.log_event.emit(f"[WATCHDOG] Gun {gun.gun_id} timed out (> {timeout:.1f}s without packets).")
                self.gun_manager.gun_state_changed.emit(gun.gun_id)
        if changed:
            self.gun_manager.all_guns_changed.emit()

    # =========================================================================
    # SIMULATED CONTROLLER MODE (Testing Requirement)
    # =========================================================================

    def enable_simulation(self, count: int = 4):
        """Enables 4 simulated virtual guns for testing without physical microcontrollers."""
        if self.is_simulated:
            return
        self.is_simulated = True
        self.log_event.emit(f"[SIMULATION] Enabled {count} virtual gun controllers (Guns 1 to {count})")

        for gid in range(1, count + 1):
            sim_port = f"SIM-{gid}"
            self.port_to_gun[sim_port] = gid
            self.gun_to_port[gid] = sim_port
            gun = self.gun_manager.get_or_create_gun(gid)
            gun.port = sim_port
            gun.connected = True
            gun.last_heartbeat = time.time()
            # Assign initial test coordinates spread across display
            base_w = self.gun_manager.display_width
            base_h = self.gun_manager.display_height
            slot_x = int((base_w / (count + 1)) * gid)
            slot_y = int(base_h / 2)
            gun.update_aim(slot_x, slot_y, base_w, base_h)
            self.device_connected.emit(sim_port, gid)

        # Simulation keepalive timer (every 1 sec)
        self._sim_timer = QTimer(self)
        self._sim_timer.setInterval(1000)
        self._sim_timer.timeout.connect(self._on_sim_timer_tick)
        self._sim_timer.start()

    def disable_simulation(self):
        """Disables virtual simulated controllers."""
        if not self.is_simulated:
            return
        self.is_simulated = False
        if self._sim_timer:
            self._sim_timer.stop()
            self._sim_timer = None
        for gid in list(self.gun_to_port.keys()):
            port = self.gun_to_port.get(gid, "")
            if port.startswith("SIM-"):
                self.gun_to_port.pop(gid, None)
                self.port_to_gun.pop(port, None)
                gun = self.gun_manager.get_gun(gid)
                if gun:
                    gun.connected = False
                    gun.port = None
                self.device_disconnected.emit(port, gid)
        self.log_event.emit("[SIMULATION] Virtual controllers disabled")
        self.gun_manager.all_guns_changed.emit()

    def _on_sim_timer_tick(self):
        """Sends periodic simulated keepalive heartbeats."""
        if not self.is_simulated:
            return
        now = time.time()
        for gid in range(1, 5):
            gun = self.gun_manager.get_gun(gid)
            if gun and gun.port and gun.port.startswith("SIM-"):
                gun.last_heartbeat = now
                gun.connected = True
                self.gun_manager.gun_state_changed.emit(gid)

    def trigger_simulated_shot(self, gun_id: int):
        """Simulates a physical trigger pull (rising edge) and release for a Gun ID."""
        gun = self.gun_manager.get_gun(gun_id)
        if not gun:
            return

        seq = self._sim_seq.get(gun_id, 0) + 1
        self._sim_seq[gun_id] = seq

        # Press
        self._on_message_received(
            port=gun.port or f"SIM-{gun_id}",
            gun_id=gun_id,
            msg_type="trigger",
            data={"type": "trigger", "gun": gun_id, "state": 1, "seq": seq},
            raw_line=f'{{"type":"trigger","gun":{gun_id},"state":1,"seq":{seq}}}',
        )

        # Release after 100 ms
        QTimer.singleShot(100, lambda: self._on_message_received(
            port=gun.port or f"SIM-{gun_id}",
            gun_id=gun_id,
            msg_type="trigger",
            data={"type": "trigger", "gun": gun_id, "state": 0, "seq": seq + 1},
            raw_line=f'{{"type":"trigger","gun":{gun_id},"state":0,"seq":{seq + 1}}}',
        ))
