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

## Challenge 1: sender, receiver and run_test

Since 0.5.0, Challenge 1 is one file. It names its colours and three functions and hands them to `run_test`, which runs the test:

```python
from lifi_hardware import receiver, run_test, sender

COLORS = {
    "off":   (0, 0, 0),
    "white": (255, 255, 255),
}

def calibrate(readings):        # readings["white"]: Readings taken while white showed
    ...

def send_symbol(name):
    sender.led.set_color(*COLORS[name])

def receive_symbol():
    r, g, b, c = receiver.sensor.read()
    return "off"                # the name of the colour you recognised

run_test(send_symbol, receive_symbol, calibrate, symbols=COLORS)
```

Since 0.6.0 the symbols are the names in `COLORS`, in the calibration, in the two functions and in the result. Towards the server they travel as numbers, the position in `COLORS`. The first form with a list, `send_symbol(index)` and `symbols=len(COLORS)` still works and numbers the symbols from 0.

With both devices on one laptop, the module lights one LED and asks whether it sends or receives, then runs the test on its own. With one device on each of two laptops, the same file runs on both; each asks whether it sends or receives, and the course server brings them together and says when the next colour comes. The random sequence comes fresh from the server for every run, and the result appears in the team cockpit. Without the server, one laptop with both devices still runs a practice run that does not count. Details in `_challenge.py`.

The module is deliberately short and commented: from Challenge 2 on it is worth looking inside. The raw Tinkerforge interface stays within reach through `led.raw` and `sensor.raw`.
