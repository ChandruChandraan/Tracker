"""Configuration constants for Firing Simulator Tracker."""

# Unreal Engine UDP Server Settings (127.0.0.1:47000)
UNREAL_IP: str = "127.0.0.1"
UNREAL_PORT: int = 47000

# Unreal Engine Application Launch Settings
UNREAL_EXE_PATH: str = r"F:\Shooter\FiringSimulator\Unreal\FiringSim\Package\V1\Windows\FiringSim.exe"
AUTO_LAUNCH_UNREAL: bool = True
AUTO_START_OVERLAY: bool = True

# UDP Connection Monitoring & Handshake
UDP_TIMEOUT: float = 3.0
UDP_HEARTBEAT_INTERVAL: float = 1.0
HELLO_MESSAGE: bytes = b"HELLO_UNREAL"
EXPECTED_RESPONSE: str = "HELLO_PYTHON"

# Overlay Rendering Settings
OVERLAY_FPS: int = 60
# Target Display Defaults: True to default to Primary display, False to default to Secondary/Projector
DEFAULT_TO_PRIMARY_DISPLAY: bool = False
OVERLAY_TRANSPARENT: bool = True
DEBUG_LOGGING: bool = True

# Serial Port & Microcontroller Settings
AUTO_CONNECT_SERIAL: bool = True
SERIAL_BAUD_RATE: int = 115200
SERIAL_TIMEOUT_S: float = 0.2
SERIAL_HEARTBEAT_TIMEOUT_S: float = 3.0
SERIAL_AUTO_SCAN_INTERVAL_MS: int = 1000
SIMULATED_GUN_COUNT: int = 4

# Distinct Color Palette per Gun ID
GUN_COLORS: dict[int, str] = {
    1: "#00ff66",  # Neon Green
    2: "#58a6ff",  # Bright Cyan / Blue
    3: "#ff7b72",  # Coral Red
    4: "#d29922",  # Amber / Gold
    5: "#bc8cff",  # Purple
    6: "#f0883e",  # Orange
    7: "#39d353",  # Mint
    8: "#f778ba",  # Hot Pink
}
