# Desktop gas sign

![Desktop gas sign](assets/icon.png)

Desktop gas sign.

Version 1.9.

This program puts fuel prices on your desk sign.

## Start here

1. Download Desktop Gas Sign.exe from the GitHub release.
2. Double-click the file.
3. Plug the sign into the PC.
4. Follow the window.

The first screen shows where the wires go.
The next screen loads the program onto the sign.
After that, type a city and click Look up.
Or type your own regular and diesel prices and click Set prices.

Click Connect so the sign can show the numbers.
Closing the window does not stop the program.
It keeps updating prices in the system tray.
Update every is on the price page. The usual choice is 30 minutes.
Right-click the tray icon and choose Quit to stop.

## The code

This GitHub project contains all of the code.
The Arduino sketch is in arduino/gas_sign_pro_micro.
The PC app is in pc/.
People who only want the program download Desktop Gas Sign.exe from the Releases page.
People who want to change it can fork the repository.

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
