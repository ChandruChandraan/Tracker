/*
 * ============================================================================
 * Firing Simulator — ESP32-C3 Gun Controller Firmware
 * ============================================================================
 * Project:      Firing Simulator Tracker
 * Hardware:     ESP32-C3 (SuperMini, DevKitM-1, XIAO ESP32-C3, etc.)
 * Framework:    Arduino
 * Baud Rate:    115200 baud
 * Protocol:     Newline-delimited JSON over USB Serial
 *
 * Each gun controller is compiled with a unique GUN_ID (1, 2, 3, 4...).
 * When the trigger button is pressed/released, JSON events are sent
 * to the host Python application over USB Serial.
 * ============================================================================
 */
// ============================================================================
// 1. HARDWARE & CONTROLLER CONFIGURATION
// ============================================================================

// Unique identifier for this gun controller (Change for each board: 1, 2, 3, 4...)
#define GUN_ID 2

// Firmware version reported in the hello handshake
#define FIRMWARE_VERSION 1

// USB Serial communication speed
#define SERIAL_BAUD_RATE 115200

// Digital GPIO connected to trigger push button (wired between GPIO and GND)
// Active-LOW with internal pull-up: Released = HIGH (0), Pressed = LOW (1)
// Avoid: GPIO 18/19 (Native USB D-/D+), GPIO 2/8/9 (Strapping/Bootloader pins)
#define TRIGGER_PIN 4

// Software debounce duration in milliseconds to prevent contact chatter
#define DEBOUNCE_DELAY_MS 25

// Keepalive heartbeat interval in milliseconds (Host watchdog timeout is 3000 ms)
#define HEARTBEAT_INTERVAL_MS 1000

// ============================================================================
// 2. GLOBAL STATE
// ============================================================================

static uint32_t g_sequence_number = 0;      // Monotonic trigger event counter
static uint32_t g_last_debounce_time = 0;   // Timestamp of last detected pin transition
static uint32_t g_last_heartbeat_time = 0;  // Timestamp of last transmitted heartbeat
static uint8_t  g_last_raw_reading = 0;     // Previous raw logical reading (0=Released, 1=Pressed)
static uint8_t  g_stable_trigger_state = 0; // Debounced confirmed state (0=Released, 1=Pressed)

// ============================================================================
// 3. SERIAL JSON DISPATCHERS
// ============================================================================

// Transmits startup handshake: {"type":"hello","gun":1,"version":1}
static void sendHelloMessage() {
  Serial.printf("{\"type\":\"hello\",\"gun\":%u,\"version\":%u}\n",
                (unsigned int)GUN_ID, (unsigned int)FIRMWARE_VERSION);
}

// Transmits periodic keepalive heartbeat: {"type":"heartbeat","gun":1}
static void sendHeartbeatMessage() {
  Serial.printf("{\"type\":\"heartbeat\",\"gun\":%u}\n", (unsigned int)GUN_ID);
}

// Transmits trigger press or release event: {"type":"trigger","gun":1,"state":1,"seq":1}
static void sendTriggerMessage(uint8_t state) {
  g_sequence_number++;
  Serial.printf("{\"type\":\"trigger\",\"gun\":%u,\"state\":%u,\"seq\":%lu}\n",
                (unsigned int)GUN_ID, (unsigned int)state, (unsigned long)g_sequence_number);
}

// ============================================================================
// 4. SETUP
// ============================================================================

void setup() {
  Serial.begin(SERIAL_BAUD_RATE);

  // Allow native USB CDC on ESP32-C3 time to enumerate if host is connecting
  uint32_t start_wait = millis();
  while (!Serial && (millis() - start_wait < 2000)) {
    delay(10);
  }

  // Switch wired between TRIGGER_PIN and GND:
  //   - Unpressed (open circuit)   = HIGH via internal pull-up -> logical 0
  //   - Pressed   (closed circuit) = LOW  (0V / GND)          -> logical 1
  pinMode(TRIGGER_PIN, INPUT_PULLUP);

  // Read initial physical pin state
  uint8_t initial_reading = (digitalRead(TRIGGER_PIN) == LOW) ? 1 : 0;
  g_last_raw_reading = initial_reading;
  g_stable_trigger_state = initial_reading;

  uint32_t now = millis();
  g_last_debounce_time = now;
  g_last_heartbeat_time = now;

  // Send startup hello packet to host
  sendHelloMessage();
}

// ============================================================================
// 5. MAIN LOOP (Non-blocking timing via millis())
// ============================================================================

void loop() {
  uint32_t current_time = millis();

  // 1. Transmit keepalive heartbeat every HEARTBEAT_INTERVAL_MS
  if ((current_time - g_last_heartbeat_time) >= HEARTBEAT_INTERVAL_MS) {
    g_last_heartbeat_time = current_time;
    sendHeartbeatMessage();
  }

  // 2. Read instantaneous logical pin state (LOW = 1 Pressed, HIGH = 0 Released)
  uint8_t raw_reading = (digitalRead(TRIGGER_PIN) == LOW) ? 1 : 0;

  // If pin reading transitioned or flickered, reset debounce timer
  if (raw_reading != g_last_raw_reading) {
    g_last_debounce_time = current_time;
    g_last_raw_reading = raw_reading;
  }

  // 3. Evaluate state only after reading has remained stable beyond debounce window
  if ((current_time - g_last_debounce_time) >= DEBOUNCE_DELAY_MS) {
    if (raw_reading != g_stable_trigger_state) {
      g_stable_trigger_state = raw_reading;
      sendTriggerMessage(g_stable_trigger_state);
    }
  }
}
