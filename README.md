# Desktop gas sign

A desk sign with two 4-digit displays. A Windows program looks up a fuel price for a city and sends it over a USB cable to an Arduino Pro Micro. The board has no Wi-Fi.

Version 1.10.

Project: https://github.com/epjarv/desktop-gas-sign

## Where the prices come from

You type a city. The app finds that place on a map and uses the nearest published figure. Canada is checked first.

Quebec uses station prices from the Régie de l'énergie, via gasquebec.ca. The app averages the 3 nearest stations.

If the nearest published Canadian place is a market in the Government of Ontario weekly fuel file, the app uses that weekly survey.

Other Canadian cities use Natural Resources Canada daily averages, matched to the nearest published city. The Canada-wide average is not used while a city series exists.

United States prices are weekly retail averages from the U.S. Energy Information Administration for the nearest city or metro. They are not prices from individual US stations.

United Kingdom prices are the 3 nearest stations from UK Government Fuel Finder, via fuelcosts.co.uk.

Canada is shown in cents per litre, the United States in dollars per gallon, and the United Kingdom in pence per litre. If you type a regular price and a diesel price, those numbers replace the lookup.

## How the numbers reach the Arduino

The Pro Micro has no radio. The Windows app turns each price into four digits plus a decimal, which is all a TM1637 display can show, and writes short lines on USB serial at 115200 baud:

- `R178.9` is regular
- `D275.9` is diesel
- `B4` is brightness, from 0 to 7

A US gallon price uses the same four digits, for example `R3.499`.

Opening the USB port resets the board. The sketch prints `READY`. The app waits for that, then keeps the port open. It looks up again on a timer while it sits in the system tray. The usual interval is 30 minutes. You can set 15 to 240. Closing the window does not quit. Quit is on the tray icon.

There are two TM1637 4-digit displays. They share 5V and GND. The top display uses pin 4 for CLK and pin 5 for DIO. The bottom display uses pin 6 for CLK and pin 7 for DIO.

## How this was built

This project was built in Cursor with an AI coding assistant (Grok, in the Cursor agent). The assistant wrote the Arduino sketch, the PC app, the GitHub packaging, and much of this README. The owner directed the product: two displays, a Pro Micro, live area prices, and a DIY case. They chose the parts, wired the sign, tested the hardware, and asked for changes. A lot of the software was generated with AI, then tested and steered by them. The assistant did not design the physical sign.

## Download

1. Download Desktop Gas Sign.exe from the GitHub release.
2. Double-click the file.
3. Plug the sign into the PC.
4. Follow the window.

The first screen shows where the wires go. The next screen can load the program onto the sign. Choose Skip if this sign already has the program. Then type a city and click Look up, or type your own prices and click Set prices. Click Connect so the sign can show the numbers.

The Arduino sketch is in arduino/gas_sign_pro_micro. The PC app is in pc/.

## Bill of materials

- Green TM1637 display: https://www.aliexpress.com/item/1005007367688224.html
- Red TM1637 display: https://www.aliexpress.com/item/1005007367688224.html
  Both colors are the same listing.
  Use 4-digit displays with decimal points on the digits, not a clock module that only has a colon. Green sits on top in the case.
- Arduino Pro Micro, 5V / 16 MHz: https://www.aliexpress.com/item/1005008228645771.html
- A USB-C data cable that fits the Pro Micro. It must carry data. A charge-only cable will not work, and the sign will look like it failed.
- Jumper wires.

## 3D files

The case is hosted on MakerWorld.

MakerWorld link: add the model URL here.
