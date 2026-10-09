import json
import socket
import threading
import time
from typing import Optional, Dict, Any

from PySide6.QtCore import QObject, Signal

from core import config


class UDPClient(QObject):
    """
    Manages non-blocking UDP communication with Unreal Engine 5.8 (127.0.0.1:47000).
    Sends pure JSON packets adhering to Unreal's FTrackerEvent deserializer.
    Maintains persistent socket and monitors connection state via HELLO_UNREAL / HELLO_PYTHON.
    """

    # Connection state signal
    connection_status_changed = Signal(bool)
    # Emits total count of packets sent
    stats_updated = Signal(int)
    # Emits telemetry for UI feedback: (data_dict, json_string)
    shot_sent = Signal(dict, str)

    def __init__(self, ip: str = config.UNREAL_IP, port: int = config.UNREAL_PORT):
        super().__init__()
        self.ip: str = ip
        self.port: int = port

        self._sock: Optional[socket.socket] = None
        self._is_running: bool = False
        self._send_lock = threading.Lock()
        self._worker_thread: Optional[threading.Thread] = None
        self.packet_count: int = 0

        # Connection Handshake State
        self.is_connected: bool = False
        self.last_received_time: float = 0.0

    def connect_client(self):
        """Initializes persistent UDP socket and starts the background listener/heartbeat thread."""
        if self._is_running:
            return

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(0.5)

        # On Windows, suppress WSAECONNRESET (10054) when remote UDP port is unreachable
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            try:
                self._sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except OSError:
                pass

        self._is_running = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()

    def disconnect_client(self):
        """Closes the UDP socket cleanly."""
        self._is_running = False

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
        self._worker_thread = None

        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

        if self.is_connected:
            self.is_connected = False
            self.connection_status_changed.emit(False)

    def send_raw_bytes(self, data: bytes):
        """Sends raw bytes to Unreal Engine UDP server."""
        if not self._sock or not self._is_running:
            return

        with self._send_lock:
            try:
                self._sock.sendto(data, (self.ip, self.port))
            except (OSError, socket.error):
                pass

    def send_json(self, payload: Dict[str, Any]) -> str:
        """Serializes dictionary to UTF-8 JSON and transmits via UDP to Unreal."""
        if not self._sock or not self._is_running:
            return ""

        json_str = json.dumps(payload)
        with self._send_lock:
            try:
                self._sock.sendto(json_str.encode("utf-8"), (self.ip, self.port))
                self.packet_count += 1
                self.stats_updated.emit(self.packet_count)
            except (OSError, socket.error):
                pass

        return json_str

    def send_shot(
        self,
        gun: int,
        lane: int,
        x: int,
        y: int,
        cross_lane: bool,
        round_num: int = 1,
        display_width: int = 1920,
        display_height: int = 1080,
    ):
        """
        Sends SHOT JSON packet to Unreal with viewport resolution information:
        {
            "type": "shot",
            "gun": 1,
            "lane": 1,
            "x": 1920,
            "y": 1080,
            "cross_lane": false,
            "round": 1,
            "display_width": 3840,
            "display_height": 2160
        }
        """
        payload = {
            "type": "shot",
            "gun": gun,
            "lane": lane,
            "x": x,
            "y": y,
            "cross_lane": bool(cross_lane) if gun == 1 else False,
            "round": round_num,
            "display_width": display_width,
            "display_height": display_height,
        }
        json_str = self.send_json(payload)
        self.shot_sent.emit(payload, json_str)

        if getattr(config, "DEBUG_LOGGING", True):
            print(f"[UDP] SHOT (Gun {gun}, Lane {lane})\nX={x}\nY={y}\nRound={round_num}\nResolution={display_width}x{display_height}\n")

    def _run_loop(self):
        """
        Background worker thread:
        Periodically sends HELLO_UNREAL handshake probe and listens for HELLO_PYTHON.
        Updates is_connected based on Unreal's responses (UDP_TIMEOUT).
        """
        last_heartbeat_sent = 0.0

        while self._is_running and self._sock:
            now = time.time()

            # Periodic handshake probe to verify Unreal is listening
            if now - last_heartbeat_sent >= config.UDP_HEARTBEAT_INTERVAL:
                self.send_raw_bytes(config.HELLO_MESSAGE)
                last_heartbeat_sent = now

            # Listen for inbound responses
            try:
                data, _ = self._sock.recvfrom(1024)
                message = data.decode("utf-8", errors="replace").strip()

                if message == config.EXPECTED_RESPONSE:
                    self.last_received_time = time.time()

                    if not self.is_connected:
                        self.is_connected = True
                        self.connection_status_changed.emit(True)

            except socket.timeout:
                pass
            except (OSError, socket.error):
                pass

            # Timeout detection
            if self.is_connected:
                if time.time() - self.last_received_time > config.UDP_TIMEOUT:
                    self.is_connected = False
                    self.connection_status_changed.emit(False)

            time.sleep(0.02)
