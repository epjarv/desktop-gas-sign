#pragma once

struct FieldReader {
  char key[40];
  char value[48];
  uint8_t keyLen;
  uint8_t valueLen;
  uint8_t phase;
  bool got;
};

struct NearestCity {
  char slug[24];
  float lat;
  float lon;
  float distance;
  bool used;
};

struct CityScan {
  NearestCity best[2];
  FieldReader fields;
  char slug[24];
  bool haveSlug;
  bool haveLat;
  bool haveLng;
  float lat;
  float lon;
  float hereLat;
  float hereLon;
};

struct Station {
  float regular;
  float diesel;
  bool hasRegular;
  bool hasDiesel;
  float distance;
  bool used;
};

struct StationScan {
  Station best[3];
  char window[360];
  int len;
  float lat;
  float lon;
  int found;
};

struct CsvScan {
  char wanted[40];
  int column;
  int fuelColumn;
  int columns;
  bool header;
  char line[1600];
  int len;
  char latestRegularDate[12];
  char latestDieselDate[12];
  float regular;
  float diesel;
  bool hasRegular;
  bool hasDiesel;
};

struct UkScan {
  Station best[3];
  FieldReader fields;
  int kept;
  bool closed;
  bool hasRegular;
  bool hasDiesel;
  float regular;
  float diesel;
  char fuel[16];
};
