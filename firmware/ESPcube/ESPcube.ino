// Board: "Generic ESP8266 Module", Flash Size: "2MB" — this is an ESP8285
// (embedded 2MB flash), NOT a 4MB NodeMCU. Building for the wrong size makes
// OTA reject every image with "Flash config wrong"; check http://<cube>/info.

#include <ESP8266WiFi.h>
#include <ESPAsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <ArduinoOTA.h>
#include <IRremoteESP8266.h>
#include <IRsend.h>

const uint16_t kIrLedPin = 4;
IRsend irsend(kIrLedPin);

// Define IR codes for each command
#define BRIGHT_UP 0xFFA05F
#define BRIGHT_DOWN 0xFF20DF
#define OFF 0xFF609F
#define ON 0xFFE01F
#define R 0xFF906F
#define G 0xFF10EF
#define B 0xFF50AF
#define W 0xFFD02F
#define V 0xFF58A7
#define P 0xFF48B7
#define Y 0xFF8877
#define LG 0xFF30CF
#define O 0xFFA857
#define YO 0xFF9867
#define FLASH 0xFFF00F
#define STROBE 0xFFE817
#define FADE 0xFFD827
#define SMOOTH 0xFFC837

AsyncWebServer server(80);
AsyncWebSocket ws("/ws");

// WiFi credentials, WebSocket token, and OTA password live in secrets.h
// (gitignored). Copy secrets.h.example to secrets.h and fill it in.
#include "secrets.h"
// Control page served at "/". Regenerate with tools/embed_html.py after
// editing host/cube_control.html.
#include "webpage.h"

// The LED controller has no absolute-brightness code, so slam BRIGHT_UP
// enough times to hit max from any level; extra presses at max are ignored
// by the controller. Overshoot deliberately — these controllers vary from
// ~8 to ~20 steps, and landing mid-scale is what makes red look muddy.
const uint8_t kBrightnessSteps = 25;
// Presses closer together than this can be debounced away as one hold.
const uint16_t kBrightnessStepMs = 120;

String deviceHostname;

// Brightness presses are paced out from loop() rather than with delay(),
// so a 3-second ramp never blocks the async TCP stack mid-callback.
uint8_t pendingBumps = 0;
unsigned long lastBumpMs = 0;

void setup() {
  Serial.begin(9600);
  irsend.begin();
  setupWiFi();
  setupOTA();
  setupWebServer();
  ws.onEvent(onWebSocketEvent);
  server.addHandler(&ws);
  server.begin();

  Serial.println("Into the loop we go!");
}

void loop() {
  ArduinoOTA.handle();

  if (pendingBumps > 0 && millis() - lastBumpMs >= kBrightnessStepMs) {
    irsend.sendNEC(BRIGHT_UP, 32);
    lastBumpMs = millis();
    if (--pendingBumps == 0) {
      Serial.println("Brightness maxed");
    }
  }
}

void setupWiFi() {
  String macSuffix = getMacSuffix();            // Get MAC address suffix
  deviceHostname = "ESPcube" + macSuffix;       // Create a unique hostname

  Serial.println();
  Serial.print("Connecting to ");
  Serial.println(ssid);

  /* Explicitly set the ESP8266 to be a WiFi-client */
  WiFi.mode(WIFI_STA);
  WiFi.hostname(deviceHostname.c_str());  // Set the hostname before beginning WiFi connection
  WiFi.begin(ssid, password);

  // Blue = busy/not ready (booting, joining, updating). White = joined.
  // Red/green are reserved for meeting status so they never mean "wait".
  sendColor(ON, "ON");
  sendColor(B, "Blue");
  sendColor(STROBE, "STROBE");  // pulsing blue while joining WiFi

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("WiFi connected");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());

  // After successful connection, indicate with WHITE then off
  wifiConnectedSuccessfully();
}

void setupOTA() {
  ArduinoOTA.setHostname(deviceHostname.c_str());
  ArduinoOTA.setPassword(otaPassword);

  ArduinoOTA.onStart([]() {
    // Flash writes starve the async TCP stack; shut the socket up first
    ws.enable(false);
    ws.closeAll();
    Serial.println("OTA update starting");
    // Pulsing blue for the duration of the flash. No brightness ramp here:
    // loop() is starved during the transfer, and a blocking ramp would just
    // delay the update by a few seconds.
    pendingBumps = 0;
    sendColor(ON, "ON");
    sendColor(B, "Blue");
    sendColor(STROBE, "STROBE");
  });
  ArduinoOTA.onEnd([]() {
    Serial.println("\nOTA done, rebooting");
  });
  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    Serial.printf("OTA progress: %u%%\r", progress / (total / 100));
  });
  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("OTA error [%u]: ", error);
    if (error == OTA_AUTH_ERROR) Serial.println("auth failed");
    else if (error == OTA_BEGIN_ERROR) Serial.println("begin failed");
    else if (error == OTA_CONNECT_ERROR) Serial.println("connect failed");
    else if (error == OTA_RECEIVE_ERROR) Serial.println("receive failed");
    else if (error == OTA_END_ERROR) Serial.println("end failed");
    ws.enable(true);  // update aborted; resume normal service
  });

  ArduinoOTA.begin();
  Serial.println("OTA ready");
}

void setupWebServer() {
  // The page carries no token — you type it once and the browser keeps it,
  // so serving this to the LAN doesn't hand out the ability to drive the cube.
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send_P(200, "text/html", kControlPage);
  });

  // Diagnostics. Flash real-vs-configured is the one that matters: if they
  // disagree, OTA refuses every image with "Flash config wrong", and only a
  // USB flash built for the real size can fix it.
  server.on("/info", HTTP_GET, [](AsyncWebServerRequest *request) {
    String out;
    out += "host: " + deviceHostname + "\n";
    out += "ip: " + WiFi.localIP().toString() + "\n";
    out += "flash real: " + String(ESP.getFlashChipRealSize()) + "\n";
    out += "flash configured: " + String(ESP.getFlashChipSize()) + "\n";
    out += "flash ok: " + String(ESP.getFlashChipRealSize() == ESP.getFlashChipSize() ? "yes" : "NO - OTA WILL FAIL") + "\n";
    out += "sketch: " + String(ESP.getSketchSize()) + "\n";
    out += "free sketch space: " + String(ESP.getFreeSketchSpace()) + "\n";
    out += "core: " + String(ESP.getCoreVersion()) + "\n";
    request->send(200, "text/plain", out);
  });
}

void onWebSocketEvent(AsyncWebSocket *server, AsyncWebSocketClient *client, AwsEventType type,
                      void *arg, uint8_t *data, size_t len) {
  Serial.printf("WebSocket event: %d\n", type);
  if (type == WS_EVT_CONNECT) {
    Serial.println("WebSocket client connected");
  } else if (type == WS_EVT_DISCONNECT) {
    Serial.println("WebSocket client disconnected");
  } else if (type == WS_EVT_ERROR) {
    Serial.println("WebSocket error occurred");
  } else if (type == WS_EVT_DATA) {
    String msg;
    msg.concat((const char *)data, len);  // data is not null-terminated; copy by length
    msg.trim();
    Serial.print("Data received: ");
    Serial.println(msg);

    int sep = msg.indexOf(':');
    if (sep < 0 || msg.substring(0, sep) != wsToken) {
      Serial.println("Rejected: missing or bad token");
      return;
    }
    handleColorCommand(msg.substring(sep + 1));
  }
}

String getMacSuffix() {
  uint8_t mac[6];
  WiFi.macAddress(mac);
  // Convert last four digits of the MAC address to a hexadecimal string
  String macID = String(mac[2], HEX) + String(mac[3], HEX) + String(mac[4], HEX) + String(mac[5], HEX);
  macID.toUpperCase();
  return macID;
}

void handleColorCommand(const String &command) {
  uint32_t irCode = 0;
  String colorName = "Unknown";

  String trimmedCommand = command;
  trimmedCommand.trim();  // Trim any whitespace from the command

  Serial.print("Processed Command: '");
  Serial.print(trimmedCommand);
  Serial.println("'");

  // Diagnostics: drive the controller directly, with no ON wrapper and no
  // auto-ramp, so a single IR code's effect can be observed in isolation.
  // RAW:<hex>       send one NEC frame, e.g. RAW:FF906F
  // BUMP:<n>        send BRIGHT_UP n times
  if (trimmedCommand.startsWith("RAW:")) {
    uint32_t code = strtoul(trimmedCommand.substring(4).c_str(), nullptr, 16);
    irsend.sendNEC(code, 32);
    Serial.print("RAW 0x");
    Serial.println(code, HEX);
    return;
  }
  if (trimmedCommand.startsWith("BUMP:")) {
    pendingBumps = trimmedCommand.substring(5).toInt();
    lastBumpMs = millis();
    Serial.printf("BUMP x%d queued\n", pendingBumps);
    return;
  }

  if (trimmedCommand == "R") {
    irCode = R;
    colorName = "Red";
  } else if (trimmedCommand == "G") {
    irCode = G;
    colorName = "Green";
  } else if (trimmedCommand == "B") {
    irCode = B;
    colorName = "Blue";
  } else if (trimmedCommand == "W") {
    irCode = W;
    colorName = "White";
  } else if (trimmedCommand == "V") {
    irCode = V;
    colorName = "Violet";
  } else if (trimmedCommand == "P") {
    irCode = P;
    colorName = "Purple";
  } else if (trimmedCommand == "Y") {
    irCode = Y;
    colorName = "Yellow";
  } else if (trimmedCommand == "LG") {
    irCode = LG;
    colorName = "Light Green";
  } else if (trimmedCommand == "O") {
    irCode = O;
    colorName = "Orange";
  } else if (trimmedCommand == "YO") {
    irCode = YO;
    colorName = "Yellow Orange";
  } else if (trimmedCommand == "FLASH") {
    irCode = FLASH;
    colorName = "Flash";
  } else if (trimmedCommand == "STROBE") {
    irCode = STROBE;
    colorName = "Strobe";
  } else if (trimmedCommand == "FADE") {
    irCode = FADE;
    colorName = "Fade";
  } else if (trimmedCommand == "SMOOTH") {
    irCode = SMOOTH;
    colorName = "Smooth";
  } else if (trimmedCommand == "ON") {
    irCode = ON;
    colorName = "On";
  } else if (trimmedCommand == "OFF") {
    irCode = OFF;
    colorName = "Off";
  } else if (trimmedCommand == "BRIGHT_UP") {
    irCode = BRIGHT_UP;
    colorName = "Bright Up";
  } else if (trimmedCommand == "BRIGHT_DOWN") {
    irCode = BRIGHT_DOWN;
    colorName = "Bright Down";
  }

  if (irCode != 0) {
    sendColor(ON, "ON");  // Change to ON
    sendColor(irCode, colorName);
    // Picking a color resets the controller to its own (dim) default level,
    // so re-ramp after every color switch. Skipped for OFF and for the
    // manual brightness keys, which would otherwise fight the operator.
    if (trimmedCommand != "OFF" && trimmedCommand != "BRIGHT_UP" &&
        trimmedCommand != "BRIGHT_DOWN") {
      pendingBumps = kBrightnessSteps;  // paced out from loop()
      lastBumpMs = millis();
    }
  } else {
    Serial.println("Received unknown command: " + command);
  }
}

void sendColor(uint32_t colorCode, String colorName) {
  irsend.sendNEC(colorCode, 32);
  Serial.print("Sent ");
  Serial.print(colorName);
  Serial.print(" color, IR Code: 0x");
  Serial.println(colorCode, HEX);
}

void maxBrightness() {
  for (uint8_t i = 0; i < kBrightnessSteps; i++) {
    delay(kBrightnessStepMs);  // discrete presses, not NEC repeat frames
    irsend.sendNEC(BRIGHT_UP, 32);
  }
  Serial.println("Brightness maxed");
}

// Function to display 'Connection Failed' status
void wifiConnectionFailed() {
  sendColor(SMOOTH, "Smooth");  // Start with smooth effect
  delay(100);                   // Wait for 100ms
  sendColor(R, "Red");          // Change to red
}

// Function to display 'Connection Successful' status
void wifiConnectedSuccessfully() {
  sendColor(W, "White");  // solid white cancels the blue strobe
  maxBrightness();
  delay(3000);            // Display white for 3000ms
  sendColor(OFF, "OFF");  // Change to off
}
