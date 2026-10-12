# lifi-hardware

The readable interface to the LED and the colour sensor of the LiFi project at Hochschule Osnabrück (Tinkerforge Master Brick, RGB LED Bricklet 2.0, Color Bricklet 2.0).

## Installing

```
pip install git+https://github.com/winf-hsos/lifi-hardware.git
```

Requirements: the Brick Daemon is running, and the device is plugged into a USB port.

## Using it

```python
from lifi_hardware import led, sensor

led.set_color(255, 0, 0)          # send red
reading = sensor.read()           # Reading(r, g, b, c)
print(reading)

sensor.set_integration_time(24)   # 2.4, 24, 101, 154 or 700 ms
sensor.set_gain(16)               # 1, 4, 16 or 60
```

`led` and `sensor` connect by themselves on their first use, and when the program ends the LED goes off and the connection is closed. If you want to decide yourself when to connect and disconnect, use the class behind them:

```python
from lifi_hardware import LifiDevice

lifi = LifiDevice.connect()
lifi.led.set_color(255, 0, 0)
print(lifi.sensor.read())
lifi.close()
```

The two settings of the sensor (integration time and gain) are visible on purpose: they are the central trade-off of the project, and you are meant to turn them. Everything the device does also goes into a measurement log, `lifi_log.jsonl` in the working folder (`LifiDevice.connect(log_file=None)` switches it off).

The measurement log also goes to the course server, so that you can see your link live in the browser and the challenge tests can run. What is uploaded is exactly what is in your local file, nothing more; readings are bundled into packets. You can switch this off at any time and without any disadvantage with `LifiDevice.connect(server=None)` or the environment variable `LIFI_SERVER=off`. Without network or server everything works the same, only without the live view. The details are in `_telemetry.py`.

## Two devices on one computer

The LED and the sensor always come from the same master brick. If two devices hang on the same computer, for example at the front under the visualizer, each script says which one it means by the UID of its master brick (shown in the Brick Viewer); after that, `led` and `sensor` work as usual:

```python
from lifi_hardware import led, sensor, use_device

use_device("64SK5w")          # before the first use of led or sensor
led.set_color(255, 0, 0)
```

The same works without changing the code through the environment variable `LIFI_MASTER=64SK5w`, and with `LifiDevice.connect(master="64SK5w")`. If two devices are plugged in and a script does not choose, it gets an error message that names both UIDs instead of a device made of the LED of one and the sensor of the other.

The module is deliberately short and commented: from Challenge 2 on it is worth looking inside. The raw Tinkerforge interface stays within reach through `led.raw` and `sensor.raw`.
