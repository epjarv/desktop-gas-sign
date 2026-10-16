/*
  Desktop gas sign — Pro Micro, two TM1637 displays.

  Install "TM1637" by Avishay Orpaz from the Arduino Library Manager.
  The header that library ships is TM1637Display.h. Listings that say
  TM1637.h mean this library.

  Wiring (5V Pro Micro). Follow the labels printed on each module.
  Power is shared. Each display gets its own CLK and DIO.

      Pro Micro          Regular display         Diesel display
      VCC -------------- VCC ------------------- VCC
      GND -------------- GND ------------------- GND
      4   -------------- CLK
      5   -------------- DIO
      6   -------------------------------------- CLK
      7   -------------------------------------- DIO

  The PC app sends one line per command, 115200 baud:

      R4.331     regular price, decimal anywhere among 4 digits
      D6.259     diesel price
      R----      dashes (no price yet)
      B4         brightness 0 (dim) through 7 (bright)

  Opening the USB port restarts a Pro Micro. The sketch prints READY
  when the displays are up. The PC app waits for that, then keeps the
  port open so later prices update immediately.
*/

#include <TM1637Display.h>

const uint8_t REGULAR_CLK = 4;
const uint8_t REGULAR_DIO = 5;
const uint8_t DIESEL_CLK = 6;
const uint8_t DIESEL_DIO = 7;

const uint8_t DASH_SEGMENT = 0x40;

TM1637Display regularDisplay(REGULAR_CLK, REGULAR_DIO);
TM1637Display dieselDisplay(DIESEL_CLK, DIESEL_DIO);

uint8_t brightness = 4;
char lastRegular[8] = "----";
char lastDiesel[8] = "----";

char line[24];
uint8_t lineLength = 0;
bool lineOverflow = false;

void remember(char *slot, const char *text) {
  strncpy(slot, text, 7);
  slot[7] = '\0';
}

void showDashes(TM1637Display &display) {
  uint8_t segments[4] = {DASH_SEGMENT, DASH_SEGMENT, DASH_SEGMENT, DASH_SEGMENT};
  display.setSegments(segments);
}

void showLamp() {
  uint8_t segments[4] = {0xFF, 0xFF, 0xFF, 0xFF};
  regularDisplay.setSegments(segments);
  dieselDisplay.setSegments(segments);
}

// "4.331" lights up as 4.331. "179.9" lights up as 179.9.
// The leftmost decimal point is bit 0x80.
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
  uint8_t decimalMask = 0x80 >> decimalOn;
  display.showNumberDecEx(value, decimalMask, true, 4, 0);
  return true;
}

void showSavedPrices() {
  showPrice(regularDisplay, lastRegular);
  showPrice(dieselDisplay, lastDiesel);
}

void trimLine(char *text) {
  char *start = text;
  while (*start == ' ' || *start == '\t') {
    start++;
  }
  if (start != text) {
    memmove(text, start, strlen(start) + 1);
  }

  size_t length = strlen(text);
  while (length > 0 && (text[length - 1] == ' ' || text[length - 1] == '\t' || text[length - 1] == '\r')) {
    text[--length] = '\0';
  }
}

void rejectLine() {
  Serial.println(F("ERR"));
}

void handleLine(char *text) {
  trimLine(text);
  if (text[0] == '\0') {
    return;
  }

  char command = text[0];
  if (command >= 'a' && command <= 'z') {
    command = command - 'a' + 'A';
  }

  if (command == 'B') {
    const char *value = text + 1;
    while (*value == ' ') {
      value++;
    }
    if (value[0] < '0' || value[0] > '7' || value[1] != '\0') {
      rejectLine();
      return;
    }
    brightness = value[0] - '0';
    regularDisplay.setBrightness(brightness, true);
    dieselDisplay.setBrightness(brightness, true);
    showSavedPrices();
    Serial.print(F("OK B "));
    Serial.println(brightness);
    return;
  }

  if (command == 'R' || command == 'D') {
    const char *price = text + 1;
    while (*price == ' ') {
      price++;
    }

    TM1637Display &display = (command == 'R') ? regularDisplay : dieselDisplay;
    char *saved = (command == 'R') ? lastRegular : lastDiesel;
    if (!showPrice(display, price)) {
      rejectLine();
      return;
    }
    remember(saved, price);
    Serial.print(F("OK "));
    Serial.print(command);
    Serial.print(' ');
    Serial.println(saved);
    return;
  }

  rejectLine();
}

void setup() {
  Serial.begin(115200);

  regularDisplay.setBrightness(brightness, true);
  dieselDisplay.setBrightness(brightness, true);
  showLamp();
  delay(500);
  showDashes(regularDisplay);
  showDashes(dieselDisplay);
  remember(lastRegular, "----");
  remember(lastDiesel, "----");

  unsigned long started = millis();
  while (!Serial && millis() - started < 2500) {
    delay(10);
  }
  Serial.println(F("READY"));
}

void loop() {
  while (Serial.available() > 0) {
    char next = Serial.read();
    if (next == '\r') {
      continue;
    }
    if (next == '\n') {
      line[lineLength] = '\0';
      if (!lineOverflow) {
        handleLine(line);
      } else {
        rejectLine();
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
}
