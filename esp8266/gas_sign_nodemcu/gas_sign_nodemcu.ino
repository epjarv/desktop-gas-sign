/*
  Desktop gas sign, ESP8266 edition. Lolin NodeMCU v3.

  After Wi-Fi and a city are saved, the board updates by itself.
  The PC app is only for the first setup and for flashing.

  Wiring, one header on each TM1637 (the second header is the same pins):
    VIN  -> both VCC (5V when the board is on USB)
    GND  -> both GND
    D1 GPIO5  -> top display CLK
    D2 GPIO4  -> top display DIO
    D5 GPIO14 -> bottom display CLK
    D6 GPIO12 -> bottom display DIO
  GPIO0, GPIO2, and GPIO15 are left alone. They decide how the board boots.

  HTTPS uses BearSSL with setInsecure(). This DIY sign does not check
  the server certificate. The price is still the figure the server sent.

  Serial is 115200, one command per line. Fields are separated by a tab
  so a network name may contain spaces. Example only, not a real password:
    SCAN
    WIFI<TAB>network-name<TAB>password
    CITY<TAB>city name
    INTERVAL<TAB>30
    SAVE
*/

#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <EEPROM.h>
#include <TM1637Display.h>
#include "places.h"
#include "sign_types.h"

const uint8_t REGULAR_CLK = D1;
const uint8_t REGULAR_DIO = D2;
const uint8_t DIESEL_CLK = D5;
const uint8_t DIESEL_DIO = D6;

const uint32_t CONFIG_MAGIC = 0x47533131;
const unsigned long WIFI_WAIT_MS = 25000;
const int BODY_LIMIT = 500000;

TM1637Display regularDisplay(REGULAR_CLK, REGULAR_DIO);
TM1637Display dieselDisplay(DIESEL_CLK, DIESEL_DIO);

struct Config {
  uint32_t magic;
  char ssid[33];
  char password[65];
  char city[61];
  uint16_t interval;
  uint8_t hasGeo;
  char country[3];
  char state[24];
  float lat;
  float lon;
};

Config config;
char lastRegular[8] = "----";
char lastDiesel[8] = "----";
bool havePrice = false;
unsigned long lastFetch = 0;
bool fetchPending = false;

char line[180];
uint8_t lineLength = 0;
bool lineOverflow = false;

void showDashes(TM1637Display &display) {
  uint8_t segments[4] = {0x40, 0x40, 0x40, 0x40};
  display.setSegments(segments);
}

bool showPrice(TM1637Display &display, const char *text) {
  if (strcmp(text, "----") == 0) {
    showDashes(display);
    return true;
  }
  int digits[4];
  int count = 0;
  int decimalOn = -1;
  for (const char *cursor = text; *cursor != '\0'; cursor++) {
    if (*cursor == '.') {
      if (decimalOn >= 0 || count == 0) {
        return false;
      }
      decimalOn = count - 1;
      continue;
    }
    if (*cursor < '0' || *cursor > '9' || count >= 4) {
      return false;
    }
    digits[count++] = *cursor - '0';
  }
  if (count != 4 || decimalOn < 0 || decimalOn > 2) {
    return false;
  }
  int value = digits[0] * 1000 + digits[1] * 100 + digits[2] * 10 + digits[3];
  display.showNumberDecEx(value, 0x80 >> decimalOn, true, 4, 0);
  return true;
}

void showSaved() {
  showPrice(regularDisplay, lastRegular);
  showPrice(dieselDisplay, lastDiesel);
}

void formatFigure(float price, bool minor, char *out) {
  if (minor) {
    if (price < 0 || price >= 1000) {
      strcpy(out, "----");
      return;
    }
    int scaled = (int)(price * 10.0f + 0.5f);
    if (scaled > 9999) {
      scaled = 9999;
    }
    int whole = scaled / 10;
    int frac = scaled % 10;
    if (whole >= 100) {
      snprintf(out, 8, "%d.%d", whole, frac);
    } else {
      snprintf(out, 8, "%03d.%d", whole, frac);
    }
    return;
  }
  if (price < 0 || price >= 100) {
    strcpy(out, "----");
    return;
  }
  if (price >= 10) {
    int scaled = (int)(price * 100.0f + 0.5f);
    snprintf(out, 8, "%d.%02d", scaled / 100, scaled % 100);
  } else {
    int scaled = (int)(price * 1000.0f + 0.5f);
    snprintf(out, 8, "%d.%03d", scaled / 1000, scaled % 1000);
  }
}

void applyPrices(float regular, bool hasRegular, float diesel, bool hasDiesel, bool minor) {
  if (!hasRegular && !hasDiesel) {
    return;
  }
  if (hasRegular) {
    formatFigure(regular, minor, lastRegular);
  } else {
    strcpy(lastRegular, "----");
  }
  if (hasDiesel) {
    formatFigure(diesel, minor, lastDiesel);
  } else {
    strcpy(lastDiesel, "----");
  }
  havePrice = true;
  showSaved();
  Serial.print(F("PRICE "));
  Serial.print(lastRegular);
  Serial.print(' ');
  Serial.println(lastDiesel);
}

float kmBetween(float lat1, float lon1, float lat2, float lon2) {
  const float radius = 6371.0f;
  float p1 = lat1 * 0.0174533f;
  float p2 = lat2 * 0.0174533f;
  float dphi = (lat2 - lat1) * 0.0174533f;
  float dl = (lon2 - lon1) * 0.0174533f;
  float a = sin(dphi / 2) * sin(dphi / 2) + cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2);
  return 2 * radius * asin(sqrt(a));
}

void blankConfig() {
  memset(&config, 0, sizeof(config));
  config.magic = CONFIG_MAGIC;
  config.interval = 30;
}

void loadConfig() {
  EEPROM.begin(256);
  EEPROM.get(0, config);
  config.ssid[sizeof(config.ssid) - 1] = '\0';
  config.password[sizeof(config.password) - 1] = '\0';
  config.city[sizeof(config.city) - 1] = '\0';
  config.country[sizeof(config.country) - 1] = '\0';
  config.state[sizeof(config.state) - 1] = '\0';
  if (config.magic != CONFIG_MAGIC) {
    blankConfig();
  }
  if (config.interval < 15 || config.interval > 240) {
    config.interval = 30;
  }
}

void saveConfig() {
  config.magic = CONFIG_MAGIC;
  EEPROM.put(0, config);
  EEPROM.commit();
}

char fetchNote[80];

void setFetchNote(const char *text) {
  strncpy(fetchNote, text, sizeof(fetchNote) - 1);
  fetchNote[sizeof(fetchNote) - 1] = '\0';
}

int readByte(WiFiClientSecure &client, unsigned long started, bool *timedOut) {
  unsigned long idleStart = millis();
  while (!client.available()) {
    unsigned long now = millis();
    if (now - idleStart >= 15000UL || now - started >= 90000UL) {
      *timedOut = true;
      return -1;
    }
    if (!client.connected()) {
      return -1;
    }
    delay(1);
    yield();
  }
  return client.read();
}

bool httpsEachByte(const char *host, const char *path, bool (*onByte)(char, void *), void *ctx) {
  WiFiClientSecure client;
  client.setInsecure();
  client.setTimeout(15000);
  // BearSSL drops a record larger than this buffer. 16384 is the smallest size
  // that can read the Natural Resources Canada price page.
  client.setBufferSizes(16384, 512);
  fetchNote[0] = '\0';
  if (!client.connect(host, 443)) {
    setFetchNote("Could not open the price site.");
    return false;
  }
  client.print(F("GET "));
  client.print(path);
  client.print(F(" HTTP/1.0\r\nHost: "));
  client.print(host);
  client.print(F("\r\nUser-Agent: DesktopGasSign/1.12 (DIY desktop fuel sign)\r\nConnection: close\r\n\r\n"));

  unsigned long started = millis();
  bool timedOut = false;
  char statusLine[16];
  int statusLen = 0;
  int status = 0;
  bool statusDone = false;
  bool chunked = false;
  bool headerDone = false;
  int matched = 0;
  char roll[12];
  int rollLen = 0;
  const char *end = "\r\n\r\n";
  while (!headerDone) {
    int value = readByte(client, started, &timedOut);
    if (value < 0) {
      client.stop();
      return false;
    }
    char c = (char)value;
    if (!statusDone) {
      if (c == '\n') {
        statusLine[statusLen] = '\0';
        if (strncmp(statusLine, "HTTP/", 5) == 0) {
          const char *space = strchr(statusLine, ' ');
          if (space) {
            status = atoi(space + 1);
          }
        }
        statusDone = true;
      } else if (c != '\r' && statusLen < (int)sizeof(statusLine) - 1) {
        statusLine[statusLen++] = c;
      }
    }
    if (rollLen < (int)sizeof(roll) - 1) {
      roll[rollLen++] = (char)tolower(c);
      roll[rollLen] = '\0';
    } else {
      memmove(roll, roll + 1, sizeof(roll) - 2);
      roll[sizeof(roll) - 2] = (char)tolower(c);
      roll[sizeof(roll) - 1] = '\0';
    }
    if (strstr(roll, "chunked")) {
      chunked = true;
    }
    if (c == end[matched]) {
      matched++;
      if (matched == 4) {
        headerDone = true;
      }
    } else {
      matched = (c == end[0]) ? 1 : 0;
    }
  }
  if (timedOut) {
    client.stop();
    setFetchNote("The price page was cut off.");
    return false;
  }
  if (status != 200) {
    client.stop();
    setFetchNote("The price site returned an error.");
    return false;
  }

  int seen = 0;
  if (!chunked) {
    while (seen < BODY_LIMIT) {
      int value = readByte(client, started, &timedOut);
      if (value < 0) {
        break;
      }
      seen++;
      if (!onByte((char)value, ctx)) {
        break;
      }
    }
  } else {
    while (seen < BODY_LIMIT) {
      char sizeText[8];
      int sizeLen = 0;
      while (sizeLen < 6) {
        int value = readByte(client, started, &timedOut);
        if (value < 0) {
          client.stop();
          return seen > 0;
        }
        if (value == '\n') {
          break;
        }
        if (value != '\r') {
          sizeText[sizeLen++] = (char)value;
        }
      }
      sizeText[sizeLen] = '\0';
      int chunk = (int)strtol(sizeText, nullptr, 16);
      if (chunk <= 0) {
        break;
      }
      for (int i = 0; i < chunk; i++) {
        int value = readByte(client, started, &timedOut);
        if (value < 0) {
          client.stop();
          return false;
        }
        seen++;
        if (!onByte((char)value, ctx)) {
          client.stop();
          return true;
        }
      }
      readByte(client, started, &timedOut);
      readByte(client, started, &timedOut);
    }
  }
  client.stop();
  if (timedOut) {
    setFetchNote("The price page was cut off.");
  }
  return seen > 0 && !timedOut;
}

void urlEncode(const char *in, char *out, int outLen) {
  int j = 0;
  for (int i = 0; in[i] != '\0' && j < outLen - 4; i++) {
    unsigned char c = (unsigned char)in[i];
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.') {
      out[j++] = (char)c;
    } else if (c == ' ') {
      out[j++] = '+';
    } else {
      snprintf(out + j, 4, "%%%02X", c);
      j += 3;
    }
  }
  out[j] = '\0';
}

struct Capture {
  char data[2048];
  int len;
  bool stop;
};

Capture page;

bool captureByte(char c, void *ctx) {
  Capture *capture = (Capture *)ctx;
  if (capture->len < (int)sizeof(capture->data) - 1) {
    capture->data[capture->len++] = c;
    capture->data[capture->len] = '\0';
  } else {
    capture->stop = true;
    return false;
  }
  return true;
}

bool loadUrl(const char *host, const char *path) {
  page.len = 0;
  page.data[0] = '\0';
  page.stop = false;
  return httpsEachByte(host, path, captureByte, &page);
}

const char *findKey(const char *json, const char *key) {
  char pattern[32];
  snprintf(pattern, sizeof(pattern), "\"%s\"", key);
  const char *found = strstr(json, pattern);
  if (!found) {
    return nullptr;
  }
  found = strchr(found + strlen(pattern), ':');
  if (!found) {
    return nullptr;
  }
  found++;
  while (*found == ' ' || *found == '"') {
    found++;
  }
  return found;
}

void copyToken(const char *start, char *out, int outLen) {
  int j = 0;
  while (start[j] && start[j] != '"' && start[j] != ',' && start[j] != '}' && j < outLen - 1) {
    out[j] = start[j];
    j++;
  }
  out[j] = '\0';
}

bool geocodeCity() {
  char encoded[180];
  char path[240];
  urlEncode(config.city, encoded, sizeof(encoded));
  snprintf(path, sizeof(path), "/search?q=%s&format=jsonv2&addressdetails=1&limit=1", encoded);
  if (!loadUrl("nominatim.openstreetmap.org", path)) {
    return false;
  }
  const char *lat = findKey(page.data, "lat");
  const char *lon = findKey(page.data, "lon");
  const char *country = findKey(page.data, "country_code");
  const char *state = findKey(page.data, "state");
  if (!lat || !lon || !country) {
    return false;
  }
  config.lat = atof(lat);
  config.lon = atof(lon);
  copyToken(country, config.country, sizeof(config.country));
  for (int i = 0; config.country[i]; i++) {
    if (config.country[i] >= 'A' && config.country[i] <= 'Z') {
      config.country[i] = config.country[i] - 'A' + 'a';
    }
  }
  if (state) {
    copyToken(state, config.state, sizeof(config.state));
  } else {
    config.state[0] = '\0';
  }
  config.hasGeo = 1;
  saveConfig();
  return true;
}

bool isQuebec() {
  char folded[24];
  int j = 0;
  for (int i = 0; config.state[i] && j < (int)sizeof(folded) - 1; i++) {
    unsigned char c = (unsigned char)config.state[i];
    if (c >= 'A' && c <= 'Z') {
      c = c - 'A' + 'a';
    }
    if (c < 128) {
      folded[j++] = (char)c;
    }
  }
  folded[j] = '\0';
  return strstr(folded, "quebec") != nullptr || strstr(folded, "qubec") != nullptr;
}

void fieldEmit(FieldReader *reader) {
  reader->key[reader->keyLen] = '\0';
  reader->value[reader->valueLen] = '\0';
  reader->got = true;
  reader->phase = 0;
}

void fieldFeed(FieldReader *reader, char c) {
  reader->got = false;
  if (reader->phase == 0) {
    if (c == '"') {
      reader->phase = 1;
      reader->keyLen = 0;
    }
    return;
  }
  if (reader->phase == 1) {
    if (c == '"') {
      reader->key[reader->keyLen] = '\0';
      reader->phase = 2;
    } else if (reader->keyLen < sizeof(reader->key) - 1) {
      reader->key[reader->keyLen++] = c;
    } else {
      reader->phase = 7;
    }
    return;
  }
  if (reader->phase == 7) {
    if (c == '"') {
      reader->keyLen = 0;
      reader->key[0] = '\0';
      reader->phase = 8;
    }
    return;
  }
  if (reader->phase == 2 || reader->phase == 8) {
    if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
      return;
    }
    if (c == ':') {
      reader->phase = reader->phase == 8 ? 9 : 3;
    } else {
      reader->phase = 0;
    }
    return;
  }
  if (reader->phase == 9 || reader->phase == 3) {
    if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
      return;
    }
    bool discard = reader->phase == 9;
    if (c == '"') {
      reader->phase = discard ? 10 : 4;
      reader->valueLen = 0;
      return;
    }
    if (c == '-' || (c >= '0' && c <= '9')) {
      reader->phase = discard ? 11 : 5;
      reader->valueLen = 0;
      if (!discard) {
        reader->value[reader->valueLen++] = c;
      }
      return;
    }
    if (c == 't' || c == 'f' || c == 'n') {
      reader->phase = discard ? 11 : 6;
      reader->valueLen = 0;
      if (!discard) {
        reader->value[reader->valueLen++] = c;
      }
      return;
    }
    reader->phase = 0;
    return;
  }
  if (reader->phase == 10) {
    if (c == '"') {
      reader->phase = 0;
    }
    return;
  }
  if (reader->phase == 11) {
    if ((c >= '0' && c <= '9') || c == '.' || (c >= 'a' && c <= 'z')) {
      return;
    }
    reader->phase = 0;
    if (c == '"') {
      reader->phase = 1;
      reader->keyLen = 0;
    }
    return;
  }
  if (reader->phase == 4) {
    if (c == '"') {
      fieldEmit(reader);
    } else if (c != '\\' && reader->valueLen < sizeof(reader->value) - 1) {
      reader->value[reader->valueLen++] = c;
    }
    return;
  }
  if (reader->phase == 5 || reader->phase == 6) {
    bool number = reader->phase == 5;
    bool keep = number ? ((c >= '0' && c <= '9') || c == '.') : (c >= 'a' && c <= 'z');
    if (keep) {
      if (reader->valueLen < sizeof(reader->value) - 1) {
        reader->value[reader->valueLen++] = c;
      }
      return;
    }
    fieldEmit(reader);
    if (c == '"') {
      reader->phase = 1;
      reader->keyLen = 0;
    }
  }
}

void fieldFinish(FieldReader *reader) {
  if ((reader->phase == 5 || reader->phase == 6) && reader->valueLen > 0) {
    fieldEmit(reader);
  }
}

void commitCity(CityScan *scan) {
  if (!scan->haveSlug || !scan->haveLat || !scan->haveLng || scan->slug[0] == '\0') {
    return;
  }
  float distance = kmBetween(scan->hereLat, scan->hereLon, scan->lat, scan->lon);
  int slot = 0;
  if (scan->best[0].used && scan->best[1].used) {
    slot = scan->best[0].distance > scan->best[1].distance ? 0 : 1;
    if (distance >= scan->best[slot].distance) {
      return;
    }
  } else if (scan->best[0].used) {
    slot = 1;
  }
  strncpy(scan->best[slot].slug, scan->slug, sizeof(scan->best[slot].slug) - 1);
  scan->best[slot].slug[sizeof(scan->best[slot].slug) - 1] = '\0';
  scan->best[slot].lat = scan->lat;
  scan->best[slot].lon = scan->lon;
  scan->best[slot].distance = distance;
  scan->best[slot].used = true;
}

void noteCity(CityScan *scan) {
  if (!scan->fields.got) {
    return;
  }
  if (strcmp(scan->fields.key, "slug") == 0) {
    commitCity(scan);
    strncpy(scan->slug, scan->fields.value, sizeof(scan->slug) - 1);
    scan->slug[sizeof(scan->slug) - 1] = '\0';
    scan->haveSlug = true;
    scan->haveLat = false;
    scan->haveLng = false;
    return;
  }
  if (strcmp(scan->fields.key, "lat") == 0 && scan->haveSlug && !scan->haveLat) {
    scan->lat = atof(scan->fields.value);
    scan->haveLat = true;
    return;
  }
  if (strcmp(scan->fields.key, "lng") == 0 && scan->haveSlug && !scan->haveLng) {
    scan->lon = atof(scan->fields.value);
    scan->haveLng = true;
  }
}

bool cityByte(char c, void *ctx) {
  CityScan *scan = (CityScan *)ctx;
  fieldFeed(&scan->fields, c);
  noteCity(scan);
  return true;
}

void considerStation(StationScan *scan) {
  if (!strstr(scan->window, "prixOrdinaire") && !strstr(scan->window, "prixDiesel")) {
    return;
  }
  if (strstr(scan->window, "\"stale\":true")) {
    return;
  }
  const char *lat = findKey(scan->window, "lat");
  const char *lng = findKey(scan->window, "lng");
  const char *regular = findKey(scan->window, "prixOrdinaire");
  const char *diesel = findKey(scan->window, "prixDiesel");
  if (!lat || !lng) {
    return;
  }
  Station item;
  memset(&item, 0, sizeof(item));
  item.distance = kmBetween(scan->lat, scan->lon, atof(lat), atof(lng));
  if (regular && *regular != 'n') {
    item.regular = atof(regular);
    item.hasRegular = item.regular > 0;
  }
  if (diesel && *diesel != 'n') {
    item.diesel = atof(diesel);
    item.hasDiesel = item.diesel > 0;
  }
  if (!item.hasRegular && !item.hasDiesel) {
    return;
  }
  item.used = true;
  int slot = -1;
  for (int i = 0; i < 3; i++) {
    if (!scan->best[i].used) {
      slot = i;
      break;
    }
  }
  if (slot < 0) {
    slot = 0;
    for (int i = 1; i < 3; i++) {
      if (scan->best[i].distance > scan->best[slot].distance) {
        slot = i;
      }
    }
    if (item.distance >= scan->best[slot].distance) {
      return;
    }
  }
  scan->best[slot] = item;
  scan->found++;
}

bool stationByte(char c, void *ctx) {
  StationScan *scan = (StationScan *)ctx;
  scan->window[scan->len++] = c;
  if (scan->len >= (int)sizeof(scan->window) - 1) {
    memmove(scan->window, scan->window + 120, scan->len - 120);
    scan->len -= 120;
  }
  scan->window[scan->len] = '\0';
  if (c == '}') {
    considerStation(scan);
    scan->len = 0;
    scan->window[0] = '\0';
  }
  return true;
}

bool fetchQuebec(float *regular, bool *hasRegular, float *diesel, bool *hasDiesel) {
  static CityScan cities;
  memset(&cities, 0, sizeof(cities));
  cities.hereLat = config.lat;
  cities.hereLon = config.lon;
  if (!httpsEachByte("www.gasquebec.ca", "/api/cities?include=prices", cityByte, &cities)) {
    return false;
  }
  fieldFinish(&cities.fields);
  noteCity(&cities);
  commitCity(&cities);
  static StationScan stations;
  memset(&stations, 0, sizeof(stations));
  stations.lat = config.lat;
  stations.lon = config.lon;
  for (int i = 0; i < 2; i++) {
    if (!cities.best[i].used) {
      continue;
    }
    char path[48];
    snprintf(path, sizeof(path), "/api/cities/%s", cities.best[i].slug);
    httpsEachByte("www.gasquebec.ca", path, stationByte, &stations);
  }
  float regularSum = 0;
  float dieselSum = 0;
  int regularCount = 0;
  int dieselCount = 0;
  int used = 0;
  for (int i = 0; i < 3; i++) {
    if (!stations.best[i].used) {
      continue;
    }
    used++;
    if (stations.best[i].hasRegular) {
      regularSum += stations.best[i].regular;
      regularCount++;
    }
    if (stations.best[i].hasDiesel) {
      dieselSum += stations.best[i].diesel;
      dieselCount++;
    }
  }
  if (used == 0) {
    return false;
  }
  *hasRegular = regularCount > 0;
  *hasDiesel = dieselCount > 0;
  if (*hasRegular) {
    *regular = regularSum / regularCount;
  }
  if (*hasDiesel) {
    *diesel = dieselSum / dieselCount;
  }
  return *hasRegular || *hasDiesel;
}

struct NrcanScan {
  char window[32];
  int len;
  bool sawDate;
  float price;
  bool have;
};

bool nrcanByte(char c, void *ctx) {
  NrcanScan *scan = (NrcanScan *)ctx;
  if ((c >= '0' && c <= '9') || c == '-' || (c == '.' && scan->len > 0)) {
    if (scan->len < (int)sizeof(scan->window) - 1) {
      scan->window[scan->len++] = c;
      scan->window[scan->len] = '\0';
    }
    return true;
  }
  if (scan->len == 10 && scan->window[4] == '-' && scan->window[7] == '-') {
    scan->sawDate = true;
  } else if (scan->sawDate && scan->len > 0 && strchr(scan->window, '-') == nullptr) {
    float price = atof(scan->window);
    if (price >= 20 && price <= 500) {
      scan->price = price;
      scan->have = true;
      scan->sawDate = false;
    }
  }
  scan->len = 0;
  scan->window[0] = '\0';
  return true;
}

bool fetchNrcanOne(int locationId, const char *product, float *price) {
  char path[96];
  snprintf(
      path,
      sizeof(path),
      "/eneene/sources/pripri/prices_bycity_e.cfm?PriceYear=0&ProductID=%s&locationID=%d",
      product,
      locationId);
  NrcanScan scan;
  memset(&scan, 0, sizeof(scan));
  httpsEachByte("www2.nrcan.gc.ca", path, nrcanByte, &scan);
  if (!scan.have) {
    if (fetchNote[0] == '\0') {
      setFetchNote("The price page had no figure.");
    }
    return false;
  }
  *price = scan.price;
  return true;
}

bool fetchNrcan(float *regular, bool *hasRegular, float *diesel, bool *hasDiesel) {
  float best = 1e9f;
  int id = -1;
  for (int i = 0; i < NRCAN_COUNT; i++) {
    float distance = kmBetween(config.lat, config.lon, NRCAN_PLACES[i].lat, NRCAN_PLACES[i].lon);
    if (distance < best) {
      best = distance;
      id = NRCAN_PLACES[i].id;
    }
  }
  if (id < 0) {
    return false;
  }
  *hasRegular = fetchNrcanOne(id, "1", regular);
  *hasDiesel = fetchNrcanOne(id, "5", diesel);
  return *hasRegular || *hasDiesel;
}

int csvField(const char *line, int index, char *out, int outLen) {
  int field = 0;
  int j = 0;
  for (int i = 0; line[i] != '\0'; i++) {
    if (line[i] == ',') {
      if (field == index) {
        break;
      }
      field++;
      j = 0;
      continue;
    }
    if (field == index && j < outLen - 1) {
      out[j++] = line[i];
    }
  }
  out[j] = '\0';
  return field;
}

void csvFinish(CsvScan *scan) {
  scan->line[scan->len] = '\0';
  if (!scan->header) {
    scan->columns = 1;
    for (int i = 0; scan->line[i]; i++) {
      if (scan->line[i] == ',') {
        scan->columns++;
      }
    }
    scan->column = -1;
    scan->fuelColumn = -1;
    for (int i = 0; i < scan->columns; i++) {
      char field[40];
      csvField(scan->line, i, field, sizeof(field));
      if (strcmp(field, scan->wanted) == 0) {
        scan->column = i;
      }
      if (strcasecmp(field, "Fuel Type") == 0 || strcasecmp(field, "Type de carburant") == 0) {
        scan->fuelColumn = i;
      }
    }
    scan->header = true;
  } else if (scan->column >= 0 && scan->fuelColumn >= 0) {
    char date[16];
    char fuel[40];
    char price[16];
    csvField(scan->line, 0, date, sizeof(date));
    csvField(scan->line, scan->fuelColumn, fuel, sizeof(fuel));
    csvField(scan->line, scan->column, price, sizeof(price));
    if (strcmp(fuel, "Regular Unleaded Gasoline") == 0 && strcmp(date, scan->latestRegularDate) > 0) {
      strncpy(scan->latestRegularDate, date, sizeof(scan->latestRegularDate) - 1);
      scan->regular = atof(price);
      scan->hasRegular = scan->regular > 0;
    }
    if (strcmp(fuel, "Diesel") == 0 && strcmp(date, scan->latestDieselDate) > 0) {
      strncpy(scan->latestDieselDate, date, sizeof(scan->latestDieselDate) - 1);
      scan->diesel = atof(price);
      scan->hasDiesel = scan->diesel > 0;
    }
  }
  scan->len = 0;
}

bool csvByte(char c, void *ctx) {
  CsvScan *scan = (CsvScan *)ctx;
  if (c == '\n') {
    csvFinish(scan);
    return true;
  }
  if (c != '\r' && scan->len < (int)sizeof(scan->line) - 1) {
    scan->line[scan->len++] = c;
  }
  return true;
}

bool fetchOntario(const char *column, float *regular, bool *hasRegular, float *diesel, bool *hasDiesel) {
  static CsvScan scan;
  memset(&scan, 0, sizeof(scan));
  strncpy(scan.wanted, column, sizeof(scan.wanted) - 1);
  if (!httpsEachByte("ontario.ca", "/v1/files/fuel-prices/fueltypesall.csv", csvByte, &scan)) {
    return false;
  }
  if (scan.len > 0) {
    csvFinish(&scan);
  }
  *regular = scan.regular;
  *diesel = scan.diesel;
  *hasRegular = scan.hasRegular;
  *hasDiesel = scan.hasDiesel;
  return scan.hasRegular || scan.hasDiesel;
}

bool fetchCanada(float *regular, bool *hasRegular, float *diesel, bool *hasDiesel) {
  if (isQuebec() && fetchQuebec(regular, hasRegular, diesel, hasDiesel)) {
    return true;
  }
  float ontarioDistance = 1e9f;
  const char *column = nullptr;
  for (int i = 0; i < ONTARIO_COUNT; i++) {
    float distance = kmBetween(config.lat, config.lon, ONTARIO_PLACES[i].lat, ONTARIO_PLACES[i].lon);
    if (distance < ontarioDistance) {
      ontarioDistance = distance;
      column = ONTARIO_PLACES[i].key;
    }
  }
  float nrcanDistance = 1e9f;
  for (int i = 0; i < NRCAN_COUNT; i++) {
    float distance = kmBetween(config.lat, config.lon, NRCAN_PLACES[i].lat, NRCAN_PLACES[i].lon);
    if (distance < nrcanDistance) {
      nrcanDistance = distance;
    }
  }
  if (column && ontarioDistance <= nrcanDistance) {
    return fetchOntario(column, regular, hasRegular, diesel, hasDiesel);
  }
  return fetchNrcan(regular, hasRegular, diesel, hasDiesel);
}

bool fetchUs(float *regular, bool *hasRegular, float *diesel, bool *hasDiesel) {
  float best = 1e9f;
  const char *code = nullptr;
  for (int i = 0; i < US_COUNT; i++) {
    float distance = kmBetween(config.lat, config.lon, US_PLACES[i].lat, US_PLACES[i].lon);
    if (distance < best) {
      best = distance;
      code = US_PLACES[i].key;
    }
  }
  if (!code) {
    return false;
  }
  char path[64];
  snprintf(path, sizeof(path), "/v1/latest?region=%s", code);
  if (!loadUrl("api.thegasnews.love", path)) {
    return false;
  }
  const char *regularKey = strstr(page.data, "gasoline_regular");
  const char *dieselKey = strstr(page.data, "\"diesel\"");
  if (regularKey) {
    const char *price = findKey(regularKey, "price");
    if (price) {
      *regular = atof(price);
      *hasRegular = *regular > 0;
    }
  }
  if (dieselKey) {
    const char *price = findKey(dieselKey, "price");
    if (price) {
      *diesel = atof(price);
      *hasDiesel = *diesel > 0;
    }
  }
  return *hasRegular || *hasDiesel;
}

void commitUk(UkScan *scan) {
  if (scan->closed || (!scan->hasRegular && !scan->hasDiesel) || scan->kept >= 3) {
    return;
  }
  Station item;
  memset(&item, 0, sizeof(item));
  item.used = true;
  item.hasRegular = scan->hasRegular;
  item.hasDiesel = scan->hasDiesel;
  item.regular = scan->regular;
  item.diesel = scan->diesel;
  scan->best[scan->kept++] = item;
}

void noteUk(UkScan *scan) {
  if (!scan->fields.got) {
    return;
  }
  const char *key = scan->fields.key;
  const char *value = scan->fields.value;
  if (strcmp(key, "node_id") == 0) {
    commitUk(scan);
    scan->closed = false;
    scan->hasRegular = false;
    scan->hasDiesel = false;
    scan->fuel[0] = '\0';
    return;
  }
  if ((strcmp(key, "is_permanently_closed") == 0 || strcmp(key, "is_temporarily_closed") == 0) && strcmp(value, "true") == 0) {
    scan->closed = true;
    return;
  }
  if (strcmp(key, "isActive") == 0 && strcmp(value, "false") == 0) {
    scan->closed = true;
    return;
  }
  if (strcmp(key, "fuel_type") == 0) {
    strncpy(scan->fuel, value, sizeof(scan->fuel) - 1);
    scan->fuel[sizeof(scan->fuel) - 1] = '\0';
    return;
  }
  if (strcmp(key, "price") != 0) {
    return;
  }
  if (strcmp(scan->fuel, "E10") != 0 && strcmp(scan->fuel, "B7_STANDARD") != 0) {
    scan->fuel[0] = '\0';
    return;
  }
  float price = atof(value);
  if (price > 0 && price < 10) {
    price *= 100;
  }
  if (price <= 0) {
    scan->fuel[0] = '\0';
    return;
  }
  if (strcmp(scan->fuel, "E10") == 0) {
    scan->regular = price;
    scan->hasRegular = true;
  } else {
    scan->diesel = price;
    scan->hasDiesel = true;
  }
  scan->fuel[0] = '\0';
}

bool ukByte(char c, void *ctx) {
  UkScan *scan = (UkScan *)ctx;
  fieldFeed(&scan->fields, c);
  noteUk(scan);
  return scan->kept < 3;
}

bool fetchUk(float *regular, bool *hasRegular, float *diesel, bool *hasDiesel) {
  char path[120];
  snprintf(
      path,
      sizeof(path),
      "/api/stations?lat=%.5f&lon=%.5f&radius=40&sort=distance&perPage=12&page=1",
      config.lat,
      config.lon);
  static UkScan scan;
  memset(&scan, 0, sizeof(scan));
  httpsEachByte("fuelcosts.co.uk", path, ukByte, &scan);
  fieldFinish(&scan.fields);
  noteUk(&scan);
  commitUk(&scan);
  float regularSum = 0;
  float dieselSum = 0;
  int regularCount = 0;
  int dieselCount = 0;
  for (int i = 0; i < scan.kept; i++) {
    if (scan.best[i].hasRegular) {
      regularSum += scan.best[i].regular;
      regularCount++;
    }
    if (scan.best[i].hasDiesel) {
      dieselSum += scan.best[i].diesel;
      dieselCount++;
    }
  }
  *hasRegular = regularCount > 0;
  *hasDiesel = dieselCount > 0;
  if (*hasRegular) {
    *regular = regularSum / regularCount;
  }
  if (*hasDiesel) {
    *diesel = dieselSum / dieselCount;
  }
  return *hasRegular || *hasDiesel;
}

bool fetchPrices() {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }
  if (!config.hasGeo) {
    if (!geocodeCity()) {
      Serial.println(F("ERR PRICE The city could not be located."));
      return false;
    }
  }
  float regular = 0;
  float diesel = 0;
  bool hasRegular = false;
  bool hasDiesel = false;
  bool minor = true;
  bool ok = false;
  if (strcmp(config.country, "ca") == 0) {
    ok = fetchCanada(&regular, &hasRegular, &diesel, &hasDiesel);
  } else if (strcmp(config.country, "us") == 0) {
    minor = false;
    ok = fetchUs(&regular, &hasRegular, &diesel, &hasDiesel);
  } else if (strcmp(config.country, "gb") == 0) {
    ok = fetchUk(&regular, &hasRegular, &diesel, &hasDiesel);
  } else {
    Serial.println(F("ERR PRICE Automatic prices are not set up for that country."));
    return false;
  }
  if (!ok) {
    Serial.print(F("ERR PRICE "));
    if (fetchNote[0] == '\0') {
      Serial.println(F("The price page had no figure."));
    } else {
      Serial.println(fetchNote);
    }
    return false;
  }
  applyPrices(regular, hasRegular, diesel, hasDiesel, minor);
  return true;
}

void joinNetwork(bool announce) {
  if (config.ssid[0] == '\0') {
    if (announce) {
      Serial.println(F("ERR Type a network name first."));
    }
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);
  WiFi.begin(config.ssid, config.password);
  if (announce) {
    Serial.println(F("CONNECTING"));
  }
  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < WIFI_WAIT_MS) {
    delay(250);
    yield();
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(F("OK CONNECTED"));
    fetchPending = true;
    lastFetch = millis() - (unsigned long)config.interval * 60000UL;
  } else if (announce) {
    Serial.println(F("ERR Could not join that network."));
  }
}

void scanNetworks() {
  Serial.println(F("SCAN"));
  WiFi.mode(WIFI_STA);
  int count = WiFi.scanNetworks();
  if (count < 0) {
    Serial.println(F("ERR The Wi-Fi scan failed."));
    return;
  }
  for (int i = 0; i < count; i++) {
    String name = WiFi.SSID(i);
    name.replace("\t", " ");
    name.replace("\n", " ");
    Serial.print(name);
    Serial.print('\t');
    Serial.println(WiFi.RSSI(i));
    yield();
  }
  WiFi.scanDelete();
  Serial.println(F("SCAN_DONE"));
}

void copyField(char *dest, int destLen, const char *src) {
  strncpy(dest, src, destLen - 1);
  dest[destLen - 1] = '\0';
}

void handleLine(char *text) {
  while (*text == ' ' || *text == '\t') {
    text++;
  }
  if (*text == '\0') {
    return;
  }
  if (strncmp(text, "SCAN", 4) == 0) {
    scanNetworks();
    return;
  }
  if (strncmp(text, "WIFI", 4) == 0) {
    char *first = strchr(text, '\t');
    char *second = first ? strchr(first + 1, '\t') : nullptr;
    if (!first || !second || first[1] == '\0') {
      Serial.println(F("ERR The network line needs a name and a password, separated by tabs."));
      return;
    }
    *second = '\0';
    copyField(config.ssid, sizeof(config.ssid), first + 1);
    copyField(config.password, sizeof(config.password), second + 1);
    Serial.println(F("OK WIFI"));
    return;
  }
  if (strncmp(text, "CITY", 4) == 0) {
    const char *value = text + 4;
    if (*value == '\t') {
      value++;
    } else {
      while (*value == ' ') {
        value++;
      }
    }
    if (*value == '\0') {
      Serial.println(F("ERR Type a city."));
      return;
    }
    copyField(config.city, sizeof(config.city), value);
    config.hasGeo = 0;
    Serial.println(F("OK CITY"));
    return;
  }
  if (strncmp(text, "INTERVAL", 8) == 0) {
    const char *value = text + 8;
    while (*value == ' ' || *value == '\t') {
      value++;
    }
    int minutes = atoi(value);
    if (minutes < 15) {
      minutes = 15;
    }
    if (minutes > 240) {
      minutes = 240;
    }
    config.interval = (uint16_t)minutes;
    Serial.print(F("OK INTERVAL "));
    Serial.println(config.interval);
    return;
  }
  if (strncmp(text, "SAVE", 4) == 0) {
    if (config.ssid[0] == '\0' || config.city[0] == '\0') {
      Serial.println(F("ERR Choose a network and type a city first."));
      return;
    }
    saveConfig();
    Serial.println(F("OK SAVE"));
    joinNetwork(true);
    return;
  }
  Serial.println(F("ERR Unknown command."));
}

void setup() {
  Serial.begin(115200);
  regularDisplay.setBrightness(4, true);
  dieselDisplay.setBrightness(4, true);
  showDashes(regularDisplay);
  showDashes(dieselDisplay);
  loadConfig();
  Serial.println(F("READY"));
  if (config.ssid[0] != '\0') {
    WiFi.mode(WIFI_STA);
    WiFi.begin(config.ssid, config.password);
  }
}

void loop() {
  while (Serial.available() > 0) {
    char next = (char)Serial.read();
    if (next == '\r') {
      continue;
    }
    if (next == '\n') {
      line[lineLength] = '\0';
      if (!lineOverflow) {
        handleLine(line);
      } else {
        Serial.println(F("ERR That line was too long."));
      }
      lineLength = 0;
      lineOverflow = false;
      continue;
    }
    if (lineLength < sizeof(line) - 1) {
      line[lineLength++] = next;
    } else {
      lineOverflow = true;
    }
  }

  if (WiFi.status() == WL_CONNECTED && config.city[0] != '\0') {
    unsigned long due = (unsigned long)config.interval * 60000UL;
    if (fetchPending || lastFetch == 0 || millis() - lastFetch >= due) {
      fetchPending = false;
      if (!fetchPrices()) {
        unsigned long due = (unsigned long)config.interval * 60000UL;
        lastFetch = millis() - due + 60000UL;
      } else {
        lastFetch = millis();
      }
    }
  }
}
