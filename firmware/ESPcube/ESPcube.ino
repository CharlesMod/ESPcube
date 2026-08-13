// Board: "Generic ESP8266 Module", Flash Size: "2MB" — this is an ESP8285
// (embedded 2MB flash), NOT a 4MB NodeMCU. Building for the wrong size makes
// OTA reject every image with "Flash config wrong"; check http://<cube>/info.

#include <ESP8266WiFi.h>
#include <ESP8266mDNS.h>
#include <WiFiUdp.h>
#include <ESPAsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <ArduinoOTA.h>
#include <IRremoteESP8266.h>
#include <IRsend.h>
#include <time.h>
#include <math.h>

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

// After dark, back off this many steps from maximum. The controller has no
// absolute-brightness code, so "night" is always expressed as a distance
// down from a known ceiling.
const uint8_t kNightDimSteps = 10;
// Sunrise/sunset is glided one step at a time so the change reads as dawn
// rather than a switch flipping: 10 steps * 90s = a 15-minute fade.
const uint32_t kGlideStepMs = 90000UL;

// Discovery: the host app broadcasts kDiscoverMagic to this port and the
// cube answers with its address, so neither end needs a hardcoded IP.
const uint16_t kDiscoveryPort = 9999;
const char *kDiscoverMagic = "ESPCUBE_DISCOVER";
WiFiUDP discoveryUdp;

String deviceHostname;

// Brightness presses are paced out from loop() rather than with delay(),
// so a long ramp never blocks the async TCP stack mid-callback. Up presses
// run before down presses, which is how "go to max, then drop N" is
// expressed without tracking an absolute level the controller won't report.
uint8_t pendingUp = 0;
uint8_t pendingDown = 0;
unsigned long lastBumpMs = 0;
uint32_t bumpIntervalMs = kBrightnessStepMs;

bool nightMode = false;
bool clockReady = false;
int lastSunCheckMinute = -1;

void setup() {
  Serial.begin(9600);
  irsend.begin();
  setupWiFi();
  setupTime();
  setupDiscovery();
  setupOTA();
  setupWebServer();
  ws.onEvent(onWebSocketEvent);
  server.addHandler(&ws);
  server.begin();

  Serial.println("Into the loop we go!");
}

void loop() {
  ArduinoOTA.handle();
  MDNS.update();
  handleDiscovery();
  checkDaylight();

  if ((pendingUp > 0 || pendingDown > 0) &&
      millis() - lastBumpMs >= bumpIntervalMs) {
    if (pendingUp > 0) {
      irsend.sendNEC(BRIGHT_UP, 32);
      pendingUp--;
    } else {
      irsend.sendNEC(BRIGHT_DOWN, 32);
      pendingDown--;
    }
    lastBumpMs = millis();
    if (pendingUp == 0 && pendingDown == 0) {
      Serial.printf("Brightness settled (%s)\n", nightMode ? "night" : "day");
    }
  }
}

// Drive brightness to the level appropriate for the time of day: all the
// way up, then back down by the night offset if it's dark out.
void applyBrightness(bool glide) {
  pendingUp = kBrightnessSteps;
  pendingDown = nightMode ? kNightDimSteps : 0;
  bumpIntervalMs = glide ? kGlideStepMs : kBrightnessStepMs;
  lastBumpMs = millis();
}

// A sunrise/sunset crossing shouldn't restate the color — only nudge the
// level of whatever is already showing, slowly.
void glideToDaylightLevel() {
  pendingUp = nightMode ? 0 : kNightDimSteps;
  pendingDown = nightMode ? kNightDimSteps : 0;
  bumpIntervalMs = kGlideStepMs;
  lastBumpMs = millis();
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

// ---------------------------------------------------------------------------
// Daylight tracking
//
// Sunrise/sunset from the standard Almanac algorithm — no network service
// involved, so the cube keeps dimming correctly even if the internet is out.
// Returns local minutes-past-midnight, or -1 above the arctic circle where
// the sun may not rise or set at all on a given date.
// ---------------------------------------------------------------------------

static double deg2rad(double d) { return d * M_PI / 180.0; }
static double rad2deg(double r) { return r * 180.0 / M_PI; }
static double clamp360(double v) {
  while (v < 0) v += 360.0;
  while (v >= 360.0) v -= 360.0;
  return v;
}

int sunEventLocalMinutes(int dayOfYear, bool rising, int tzOffsetMinutes) {
  const double zenith = 90.833;  // includes atmospheric refraction
  double lngHour = kLongitude / 15.0;
  double t = dayOfYear + (((rising ? 6.0 : 18.0) - lngHour) / 24.0);

  double M = (0.9856 * t) - 3.289;
  double L = clamp360(M + (1.916 * sin(deg2rad(M))) +
                      (0.020 * sin(deg2rad(2 * M))) + 282.634);

  double RA = clamp360(rad2deg(atan(0.91764 * tan(deg2rad(L)))));
  // RA must land in the same quadrant as L.
  RA += (floor(L / 90.0) * 90.0) - (floor(RA / 90.0) * 90.0);
  RA /= 15.0;

  double sinDec = 0.39782 * sin(deg2rad(L));
  double cosDec = cos(asin(sinDec));
  double cosH = (cos(deg2rad(zenith)) - (sinDec * sin(deg2rad(kLatitude)))) /
                (cosDec * cos(deg2rad(kLatitude)));
  if (cosH > 1 || cosH < -1) return -1;  // sun never rises/sets today

  double H = rising ? 360.0 - rad2deg(acos(cosH)) : rad2deg(acos(cosH));
  H /= 15.0;

  double T = H + RA - (0.06571 * t) - 6.622;
  double UT = T - lngHour;
  while (UT < 0) UT += 24.0;
  while (UT >= 24.0) UT -= 24.0;

  int minutes = (int)lround(UT * 60.0) + tzOffsetMinutes;
  while (minutes < 0) minutes += 1440;
  return minutes % 1440;
}

// Today's sunrise/sunset in local minutes, plus whether it's currently dark.
// Returns false if the clock isn't set or the sun doesn't rise/set today.
bool daylightNow(int *sunriseOut, int *sunsetOut, bool *darkOut) {
  if (!clockReady) return false;
  time_t now = time(nullptr);
  if (now < 100000) return false;  // NTP hasn't landed yet

  struct tm lt, gt;
  localtime_r(&now, &lt);
  gmtime_r(&now, &gt);

  int tzOffset = (lt.tm_hour * 60 + lt.tm_min) - (gt.tm_hour * 60 + gt.tm_min);
  if (tzOffset > 720) tzOffset -= 1440;
  if (tzOffset < -720) tzOffset += 1440;

  int sunrise = sunEventLocalMinutes(gt.tm_yday + 1, true, tzOffset);
  int sunset = sunEventLocalMinutes(gt.tm_yday + 1, false, tzOffset);
  if (sunrise < 0 || sunset < 0) return false;

  int nowMin = lt.tm_hour * 60 + lt.tm_min;
  *sunriseOut = sunrise;
  *sunsetOut = sunset;
  *darkOut = (sunrise < sunset) ? (nowMin < sunrise || nowMin >= sunset)
                                : (nowMin < sunrise && nowMin >= sunset);
  return true;
}

// Re-evaluate day/night once a minute; glide the brightness on a crossing.
void checkDaylight() {
  if (!clockReady) return;
  time_t now = time(nullptr);
  struct tm lt;
  localtime_r(&now, &lt);
  if (lt.tm_min == lastSunCheckMinute) return;
  lastSunCheckMinute = lt.tm_min;

  int sunrise, sunset;
  bool dark;
  if (!daylightNow(&sunrise, &sunset, &dark)) return;

  if (dark != nightMode) {
    nightMode = dark;
    Serial.printf("%s (sunrise %02d:%02d, sunset %02d:%02d) — gliding\n",
                  dark ? "Sunset" : "Sunrise", sunrise / 60, sunrise % 60,
                  sunset / 60, sunset % 60);
    glideToDaylightLevel();
  }
}

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------

void handleDiscovery() {
  int size = discoveryUdp.parsePacket();
  if (size <= 0) return;
  char buf[64] = {0};
  int len = discoveryUdp.read(buf, sizeof(buf) - 1);
  if (len <= 0) return;
  buf[len] = 0;
  if (strncmp(buf, kDiscoverMagic, strlen(kDiscoverMagic)) != 0) return;

  String reply = "ESPCUBE:" + WiFi.localIP().toString() + ":" + deviceHostname;
  discoveryUdp.beginPacket(discoveryUdp.remoteIP(), discoveryUdp.remotePort());
  discoveryUdp.write(reply.c_str());
  discoveryUdp.endPacket();
  Serial.println("Discovery reply -> " + discoveryUdp.remoteIP().toString());
}

void setupDiscovery() {
  // mDNS: reachable as espcube.local from any browser, plus a service record
  // for anything that would rather browse than broadcast.
  if (MDNS.begin("espcube")) {
    MDNS.addService("http", "tcp", 80);
    MDNS.addService("espcube", "tcp", 80);
    Serial.println("mDNS: http://espcube.local");
  } else {
    Serial.println("mDNS failed to start");
  }
  discoveryUdp.begin(kDiscoveryPort);
  Serial.printf("Discovery listening on UDP %u\n", kDiscoveryPort);
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
    pendingUp = pendingDown = 0;
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

    int sunrise, sunset;
    bool dark;
    if (daylightNow(&sunrise, &sunset, &dark)) {
      time_t now = time(nullptr);
      struct tm lt;
      localtime_r(&now, &lt);
      char buf[96];
      snprintf(buf, sizeof(buf),
               "local time: %02d:%02d\nsunrise: %02d:%02d\nsunset: %02d:%02d\n"
               "mode: %s\n",
               lt.tm_hour, lt.tm_min, sunrise / 60, sunrise % 60, sunset / 60,
               sunset % 60, dark ? "night (dimmed)" : "day (full)");
      out += buf;
    } else {
      out += "clock: not set (staying at day brightness)\n";
    }
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
    // Negative counts step down, so the bench tool can drive both directions.
    int n = trimmedCommand.substring(5).toInt();
    pendingUp = n > 0 ? n : 0;
    pendingDown = n < 0 ? -n : 0;
    bumpIntervalMs = kBrightnessStepMs;
    lastBumpMs = millis();
    Serial.printf("BUMP %d queued\n", n);
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
      applyBrightness(false);  // fast ramp, night-aware
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

// Blocking ramp — only for use during setup(), before the async server is
// serving. Everywhere else use applyBrightness(), which paces from loop().
void maxBrightness() {
  for (uint8_t i = 0; i < kBrightnessSteps; i++) {
    delay(kBrightnessStepMs);  // discrete presses, not NEC repeat frames
    irsend.sendNEC(BRIGHT_UP, 32);
  }
  Serial.println("Brightness maxed");
}

// NTP, needed only so the cube knows when the sun rises and sets. TZ string
// handles daylight saving without us tracking the rules.
void setupTime() {
  configTime(kTimezone, "pool.ntp.org", "time.nist.gov");
  Serial.print("Waiting for NTP");
  for (int i = 0; i < 40 && time(nullptr) < 100000; i++) {
    delay(250);
    Serial.print(".");
  }
  time_t now = time(nullptr);
  clockReady = now > 100000;
  if (clockReady) {
    struct tm lt;
    localtime_r(&now, &lt);
    Serial.printf("\nLocal time: %04d-%02d-%02d %02d:%02d\n", lt.tm_year + 1900,
                  lt.tm_mon + 1, lt.tm_mday, lt.tm_hour, lt.tm_min);

    // Adopt the current level outright — at boot there's no dawn to simulate,
    // so set nightMode directly rather than letting checkDaylight() glide.
    int sunrise, sunset;
    bool dark;
    if (daylightNow(&sunrise, &sunset, &dark)) {
      nightMode = dark;
      lastSunCheckMinute = lt.tm_min;
      Serial.printf("Sunrise %02d:%02d, sunset %02d:%02d — starting in %s mode\n",
                    sunrise / 60, sunrise % 60, sunset / 60, sunset % 60,
                    dark ? "night" : "day");
    }
  } else {
    Serial.println("\nNTP unavailable — staying at day brightness");
  }
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
  // Rest at green, not off: green is "nobody is on a call", which is the
  // truthful default. Red must only ever mean an open mic.
  sendColor(G, "Green");
  applyBrightness(false);
}
