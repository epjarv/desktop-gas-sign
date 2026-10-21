# Desktop gas sign

Two ways to build the same desk sign. Both use two 4-digit TM1637 displays.

Version 1.11.

Project: https://github.com/epjarv/desktop-gas-sign

The Arduino edition is a Pro Micro with no Wi-Fi. A Windows program looks up the price and sends it over USB. That program has to keep running.

The ESP8266 edition is a Lolin NodeMCU v3. You set the Wi-Fi network and the city once from the PC. After that the board joins the network and updates regular and diesel by itself. USB is only for power.

## Download

There are two programs. Pick the one that matches the board.

- Desktop Gas Sign Arduino.exe is the Pro Micro. It looks up prices on the PC.
- Desktop Gas Sign ESP8266.exe is the NodeMCU. It flashes the board, then the board fetches prices on its own.

1. Download the exe that matches your board from the GitHub release.
2. Double-click the file.
3. Plug the board into the PC with a data cable.
4. Follow the window.

The first screen is the pinout. The next screen can load the program onto the board. Choose Skip if the program is already there.

On the Arduino app, type a city and click Look up, or type your own prices and click Set prices. Click Connect so the sign can show the numbers. Closing the window leaves it in the system tray. Quit is on the tray icon.

On the ESP8266 app, scan for Wi-Fi, choose the network, type the password and a city, and save. When it says it connected, unplug it from the PC and power it from any USB port.

The Arduino sketch is in arduino/gas_sign_pro_micro. The NodeMCU sketch is in esp8266/gas_sign_nodemcu. The PC apps are in pc/.

## Where the prices come from

You type a city. The lookup finds that place on a map and uses the nearest published figure. Canada is checked first.

Quebec uses station prices from the Régie de l'énergie, via gasquebec.ca. The average is the 3 nearest stations.

If the nearest published Canadian place is a market in the Government of Ontario weekly fuel file, that weekly survey is used.

Other Canadian cities use Natural Resources Canada daily averages, matched to the nearest published city. The Canada-wide average is not used while a city series exists.

United States prices are weekly retail averages from the U.S. Energy Information Administration for the nearest city or metro that publishes its own series. They are not prices from individual US stations.

United Kingdom prices are the 3 nearest stations from UK Government Fuel Finder, via fuelcosts.co.uk.

Canada is shown in cents per litre, the United States in dollars per gallon, and the United Kingdom in pence per litre.

On the Arduino app, a typed regular price and diesel price replace the lookup. On the ESP8266, the board does this lookup itself after the city is saved. The ESP8266 does not check HTTPS certificates. BearSSL on that board is used that way for this DIY sign.

## Arduino wiring

The Pro Micro has no radio. The Windows app turns each price into four digits plus a decimal, which is all a TM1637 can show, and writes short lines on USB serial at 115200 baud:

- `R178.9` is regular
- `D275.9` is diesel
- `B4` is brightness, from 0 to 7

A US gallon price uses the same four digits, for example `R3.499`.

Opening the USB port resets the board. The sketch prints `READY`. The app waits for that, then keeps the port open. It looks up again on a timer while it sits in the system tray. The usual interval is 30 minutes. You can set 15 to 240.

Both displays share 5V and GND. Use one header on each module. The top display uses pin 4 for CLK and pin 5 for DIO. The bottom display uses pin 6 for CLK and pin 7 for DIO.

## ESP8266 wiring

Lolin NodeMCU v3. Do not use GPIO0, GPIO2, or GPIO15. Those pins decide how the board boots.

Both displays share VIN and GND. VIN is 5V when the board is powered from USB. The clock and data pins are 3.3V. A TM1637 accepts that.

Use one header on each module. The second header is the same four pins.

| NodeMCU | Top display | Bottom display |
| --- | --- | --- |
| VIN | 5V | 5V |
| GND | GND | GND |
| D1 GPIO5 | CLK | |
| D2 GPIO4 | DIO | |
| D5 GPIO14 | | CLK |
| D6 GPIO12 | | DIO |

The setup program talks to the board at 115200 baud. It asks the board to scan, then sends the network name, the password, the city, and how many minutes between updates. The board saves those and fetches prices on its own. The usual interval is 30 minutes, from 15 to 240.

## How this was built

This project was built in Cursor with an AI coding assistant (Grok, in the Cursor agent). The assistant wrote the Arduino sketch, the ESP8266 sketch, the PC apps, the GitHub packaging, and much of this README. The owner directed the product: two displays, a Pro Micro, a NodeMCU edition, live area prices, and a DIY case. They chose the parts, wired the sign, tested the hardware, and asked for changes. A lot of the software was generated with AI, then tested and steered by them. The assistant did not design the physical sign.

## Bill of materials

- Green TM1637 display: https://www.aliexpress.com/item/1005007367688224.html
- Red TM1637 display: https://www.aliexpress.com/item/1005007367688224.html
  Both colors are the same listing.
  Use 4-digit displays with decimal points on the digits, not a clock module that only has a colon. Green sits on top in the case.
- Arduino Pro Micro, 5V / 16 MHz, for the Arduino edition: https://www.aliexpress.com/item/1005008228645771.html
- Lolin NodeMCU v3 (ESP8266), for the standalone edition.
- A USB data cable. A charge-only cable will not work for setup.
- Jumper wires.

## 3D files

The case is hosted on MakerWorld.

MakerWorld link: add the model URL here.
