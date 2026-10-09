# Firing Simulator — Multi-Gun USB Serial & Projector Tracker

Python 3.12+ multi-gun controller coordinator, multi-layer projector overlay, and Unreal Engine 5.8 UDP integration.

```
Arduino Gun 1 (COMx) ──┐
Arduino Gun 2 (COMy) ──┼──► SerialDeviceManager ──► MultiGunManager ──► UDP Client ──► Unreal Engine 5.8
Arduino Gun 3 (COMz) ──┤          (115200 Baud)         │ (Isolated)     (Port 47000)
Arduino Gun 4 (COMw) ──┘                                ▼
                                              Projector Overlay
                                          (Individual Gun Layers)
```

---

## 1. Project Structure

```
Tracker/
├── Guns/
│   └── Guns.ino                 # Microcontroller firmware with configurable GUN_ID
├── python_tracker/
│   ├── main.py                  # Full-size desktop coordinator & control dashboard
│   ├── core/                    # Core state and configuration
│   │   ├── config.py            # Central configuration & color palettes
│   │   └── gun_state.py         # Independent Per-Gun State & MultiGunManager
│   ├── devices/                 # Microcontroller & hardware serial communication
│   │   └── serial_manager.py    # Non-blocking USB Serial Manager & 4-Gun Simulator
│   ├── network/                 # Unreal Engine UDP telemetry
│   │   └── udp_client.py        # Non-blocking UDP client (Port 47000)
│   ├── ui/                      # Visual overlays, displays, and window management
│   │   ├── display_manager.py   # Windows multi-display and projector discovery
│   │   ├── overlay_window.py    # Multi-layer projector overlay window
│   │   └── unreal_process.py    # Unreal process lifecycle & display positioning
│   ├── requirements.txt         # Python dependencies (PySide6, pyserial)
│   └── README.md                # Documentation and testing guide
```

---

## 2. Requirements & Installation

- **Python:** 3.12+
- **OS:** Windows 10 / 11

### Virtual Environment Setup

```powershell
cd f:\Shooter\FiringSimulator\Tracker

# Activate virtual environment
.venv\Scripts\activate

# Install dependencies
pip install -r python_tracker/requirements.txt

# Run application
python python_tracker/main.py
```

---

## 3. Microcontroller Serial Protocol

Each controller connects via USB Serial at **115200 baud** sending newline-delimited JSON:

| Event | JSON Payload | Description |
| :--- | :--- | :--- |
| **Startup Handshake** | `{"type":"hello","gun":1,"version":1}` | Identifies Gun ID and registers port |
| **Keepalive Heartbeat** | `{"type":"heartbeat","gun":1}` | Sent every 1 second (watchdog timeout = 3.0s) |
| **Trigger Pressed** | `{"type":"trigger","gun":1,"state":1,"seq":1}` | Rising-edge triggers shot |
| **Trigger Released** | `{"type":"trigger","gun":1,"state":0,"seq":2}` | Releases trigger state |

---

## 4. Multi-Gun Features

### Independent Per-Gun State (`core/gun_state.py`)
- Each gun maintains its own isolated coordinates (`latest_x`, `latest_y`), `lane`, `cross_lane`, `trigger_state`, and `shot_count`.
- **Zero coordinate leakage**: Gun 1's aim is never shared with Gun 2.
- **No fabricated data**: Shots without valid aim coordinates are flagged rather than borrowing coordinates from another gun.

### Multi-Layer Projector Overlay (`ui/overlay_window.py`)
- One single borderless fullscreen host window placed on the selected projector screen.
- Independent configurable layers for each Gun ID:
  - Distinctive visual reticle and color palette (Gun 1 Neon Green, Gun 2 Cyan, Gun 3 Coral Red, Gun 4 Amber Gold).
  - Gun ID badge, connection status dot, coordinate readout, and lane tag.
  - Per-gun shot feedback banners.
  - Independent visibility toggle per gun.
- Two Modes:
  - **Individual Tracking Mode**: Each gun reticle follows its own aim coordinates.
  - **Diagnostic Mode**: Status panel displaying real-time telemetry for all guns simultaneously.

### Non-blocking Serial Device Manager (`devices/serial_manager.py`)
- Dedicated worker threads per connected port preventing UI thread stalls.
- Auto-discovery of newly plugged controllers.
- Manual port-to-Gun-ID assignment.
- **Simulated Controller Mode**: Generates 4 virtual controllers for immediate hardware-free testing.

---

## 5. Verification & Testing

### TEST 1: Virtual Multi-Gun Simulation (No Hardware Needed)
1. Launch `python python_tracker/main.py`.
2. Click **Enable 4 Virtual Guns**.
3. **Verify:** Guns 1, 2, 3, and 4 connect in the table, receiving keepalive heartbeats.
4. Click **FIRE** next to Gun 2:
   - Only Gun 2's shot count increments.
   - An isolated Gun 2 UDP shot packet is sent to Unreal Engine (`"gun": 2`).
   - Gun 1 and Gun 3 are completely unaffected.

### TEST 2: Overlay Multi-Gun Visualization
1. With overlay active, switch between **Mode: Tracking** and **Mode: Diagnostic**.
2. Uncheck the "Overlay" checkbox for Gun 3:
   - Gun 3's layer disappears while Guns 1, 2, and 4 remain rendered.

### TEST 3: Physical Hardware Testing
1. Flash [Guns.ino](../Guns/Guns.ino) to an Arduino with `#define GUN_ID 1`.
2. Flash a second Arduino with `#define GUN_ID 2`.
3. Plug in both boards via USB.
4. Python automatically detects the incoming `hello` packets and associates each board with its respective Gun ID.
