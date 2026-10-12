"""lifi_hardware: the interface to your LiFi device.

A device carries an RGB LED (the sender) and a colour sensor (the
receiver), both made by Tinkerforge and connected through the Brick
Daemon. This module finds the parts by itself when your program starts
and gives you two handy objects, ``led`` and ``sensor``:

    from lifi_hardware import led, sensor

    led.set_color(255, 0, 0)       # send red
    reading = sensor.read()        # Reading(r, g, b, c)
    led.off()

If you want to decide yourself when to connect and disconnect, use the
class behind them:

    from lifi_hardware import LifiDevice

    lifi = LifiDevice.connect()
    lifi.led.set_color(255, 0, 0)
    reading = lifi.sensor.read()
    lifi.close()

Two settings of the sensor are visible on purpose, because they are the
central trade-off of the project (signal quality against symbol rate):
``set_integration_time()`` and ``set_gain()``. They are not hidden or
abstracted away, and you are meant to turn them.

Everything the device does also goes into a local measurement log
(``lifi_log.jsonl`` in your working folder), one line of JSON per event.
It is the memory of your measurement series and always works, even
without any network.

The raw Tinkerforge interface stays within reach (``led.raw`` and
``sensor.raw``) in case you want to go deeper.

If two devices hang on the same computer (for example at the front, under
the visualizer), each script says which one it means by the UID of its
master brick, which the Brick Viewer shows:

    from lifi_hardware import led, sensor, use_device

    use_device("64SK5w")
    led.set_color(255, 0, 0)

The same works without changing the code through the environment
variable ``LIFI_MASTER=64SK5w``.
"""

from __future__ import annotations

import json
import os
import time
from typing import NamedTuple, cast

from tinkerforge.ip_connection import IPConnection
from tinkerforge.bricklet_color_v2 import BrickletColorV2
from tinkerforge.bricklet_rgb_led_v2 import BrickletRGBLEDV2

from ._telemetry import DEFAULT_SERVER, Telemetry, install_error_hook

__version__ = "0.4.2"

# Device identifiers used by Tinkerforge (device_identifier)
_ID_RGB_LED = 2127               # RGB LED Bricklet 2.0
_ID_COLOR = 2128                 # Color Bricklet 2.0

# The sensor knows only these integration times (milliseconds) ...
_INTEGRATION_TIMES = {
    2.4: BrickletColorV2.INTEGRATION_TIME_2MS,
    24: BrickletColorV2.INTEGRATION_TIME_24MS,
    101: BrickletColorV2.INTEGRATION_TIME_101MS,
    154: BrickletColorV2.INTEGRATION_TIME_154MS,
    700: BrickletColorV2.INTEGRATION_TIME_700MS,
}
# ... and these gain factors.
_GAINS = {
    1: BrickletColorV2.GAIN_1X,
    4: BrickletColorV2.GAIN_4X,
    16: BrickletColorV2.GAIN_16X,
    60: BrickletColorV2.GAIN_60X,
}


class Reading(NamedTuple):
    """One sensor reading: red, green, blue and clear (unfiltered).

    All four values are 16-bit numbers (0 to 65535). The clear channel
    measures without a colour filter and is therefore the most sensitive
    one. The values do NOT match the RGB values that were sent, because
    overlapping filters, distance and room light lie in between, so you
    have to calibrate.
    """

    r: int
    g: int
    b: int
    c: int


class _Log:
    """The local measurement log: one JSON line per event.

    The upload companion can hang on through ``sink`` (see _telemetry.py)
    and receives exactly the same entries that are written to the file:
    local first, upload on top.
    """

    def __init__(self, path):
        self.path = path
        self.sink = None

    def write(self, event, **fields):
        record = {"t": round(time.time(), 4), "event": event, **fields}
        if self.path is not None:
            # Append instead of keeping the file open: robust against
            # crashes, and several programs can write to the same log
            # one after the other.
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        if self.sink is not None:
            self.sink(record)


class Led:
    """The sender: an RGB LED with three channels from 0 to 255."""

    def __init__(self, raw, log):
        self.raw = raw           # the raw Tinkerforge object
        self._log = log
        self.uid = raw.uid_string

    def set_color(self, r: int, g: int, b: int) -> None:
        """Sets the colour. The LED stays on until something else comes.

        Each value goes from 0 (off) to 255 (full), for example
        ``led.set_color(255, 0, 0)`` for red.
        """
        self.raw.set_rgb_value(r, g, b)
        self._log.write("set_color", r=r, g=g, b=b)

    def off(self) -> None:
        """Switches the LED off (the same as set_color(0, 0, 0))."""
        self.set_color(0, 0, 0)

    @property
    def color(self) -> tuple[int, int, int]:
        """The current colour as (r, g, b), read directly from the hardware.

        Handy for checking what is really being sent right now, for
        example after a program was stopped or while looking for a fault.
        """
        return tuple(self.raw.get_rgb_value())


class Sensor:
    """The receiver: a colour sensor with four channels.

    It has two settings. A longer integration time collects more light
    per reading, which gives calmer values but fewer readings per second;
    a higher gain makes weak light visible, and the noise as well. Both
    belong in your measurement log.
    """

    def __init__(self, raw, log):
        self.raw = raw
        self._log = log
        self.uid = raw.uid_string
        # A known starting state, so that measurement series can be
        # compared. Gain 16x, because 60x saturates the clear channel in
        # normally lit rooms even without a signal (measured 2026-09-03).
        # What suits YOUR link, only your own measurement series can tell.
        self._gain = 16
        self._integration_time = 154
        self._apply()
        # The white lamp of the sensor is meant for measuring the colour
        # of OBJECTS and only disturbs our link, so it is switched off.
        self.set_light(False)

    def _apply(self):
        self.raw.set_configuration(_GAINS[self._gain],
                                   _INTEGRATION_TIMES[self._integration_time])
        self._log.write("configure", gain=self._gain,
                        integration_time=self._integration_time)

    @property
    def integration_time(self):
        """The current integration time in milliseconds."""
        return self._integration_time

    @property
    def gain(self):
        """The current gain factor."""
        return self._gain

    def set_integration_time(self, ms: float) -> None:
        """Sets the integration time. Allowed: 2.4, 24, 101, 154, 700 ms."""
        if ms not in _INTEGRATION_TIMES:
            raise ValueError(
                f"There is no integration time of {ms} ms. "
                f"Allowed: {sorted(_INTEGRATION_TIMES)}")
        self._integration_time = ms
        self._apply()

    def set_gain(self, factor: int) -> None:
        """Sets the gain. Allowed: 1, 4, 16, 60."""
        if factor not in _GAINS:
            raise ValueError(f"There is no gain of {factor}x. "
                             f"Allowed: {sorted(_GAINS)}")
        self._gain = factor
        self._apply()

    def set_light(self, on: bool) -> None:
        """Switches the white lamp on the sensor board.

        It is meant for lighting objects (the sensor can also measure the
        colour of surfaces) and is always off at the start. Leave it off
        for the light link, otherwise the receiver dazzles itself and the
        device opposite.
        """
        self.raw.set_light(bool(on))
        self._log.write("set_light", on=bool(on))

    def read(self) -> Reading:
        """Takes one reading and returns it as Reading(r, g, b, c).

        Important: the sensor measures at its own pace, one reading per
        integration time. This call fetches the NEWEST finished reading
        and does not wait. If you read more often than the sensor
        measures, you get the same value several times; new information
        comes at most once per integration time. That is exactly why the
        integration time is a setting: calmer values against fewer
        readings per second.
        """
        r, g, b, c = self.raw.get_color()
        reading = Reading(r, g, b, c)
        self._log.write("read", r=r, g=g, b=b, c=c)
        return reading


class LifiDevice:
    """Your LiFi device: an LED and a sensor on one master brick."""

    def __init__(self, ipcon, led, sensor, log, telemetry=None, master_uid=None):
        self._ipcon = ipcon
        self.master_uid = master_uid
        self.led = led
        self.sensor = sensor
        self._log = log
        self._telemetry = telemetry

    @classmethod
    def connect(cls, host="localhost", port=4223, log_file="lifi_log.jsonl",
                server=DEFAULT_SERVER, master=None):
        """Connects to the Brick Daemon and finds the LED and the sensor.

        No configuration is needed: the parts report their unique
        identifier (UID) and their type when the program starts. The LED
        and the sensor always come from the same master brick. If several
        devices hang on the computer, ``master`` chooses one by the UID of
        its master brick; without it you get an error instead of a device
        mixed together by chance. ``log_file=None`` switches the local
        measurement log off.

        ``server`` is the course server that also receives the measurement
        log (how and why: see _telemetry.py). Switch it off at any time
        with ``server=None`` or the environment variable
        ``LIFI_SERVER=off``; without the server everything works the same,
        only without the live view.
        """
        log = _Log(log_file)
        ipcon = IPConnection()
        ipcon.connect(host, port)

        # For each master brick, the parts that hang on it:
        # {master_uid: {device_identifier: uid}}
        bricks = {}

        def on_enumerate(uid, connected_uid, position, hw_version,
                         fw_version, device_identifier, enumeration_type):
            if enumeration_type == IPConnection.ENUMERATION_TYPE_DISCONNECTED:
                return
            if device_identifier in (_ID_RGB_LED, _ID_COLOR):
                bricks.setdefault(connected_uid, {}).setdefault(device_identifier, uid)

        def complete():
            return sorted(m for m, parts in bricks.items()
                          if _ID_RGB_LED in parts and _ID_COLOR in parts)

        ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE, on_enumerate)
        ipcon.enumerate()
        # Wait briefly until all parts have reported. Once one device is
        # complete, wait a moment longer for others, so that a second
        # device on the same computer is not missed.
        deadline = time.time() + 2.0
        settle = None
        while time.time() < deadline:
            done = complete()
            if master is not None and master in done:
                break
            if master is None and done:
                settle = settle or time.time() + 0.3
                if time.time() > settle:
                    break
            time.sleep(0.05)

        devices = complete()
        if master is not None:
            chosen = master
        elif len(devices) > 1:
            ipcon.disconnect()
            raise RuntimeError(
                f"{len(devices)} devices found, with the master bricks "
                f"{', '.join(devices)}. Choose one: use_device(\"{devices[0]}\") "
                f"before the first use of led or sensor, or "
                f"LifiDevice.connect(master=\"{devices[0]}\").")
        elif devices:
            chosen = devices[0]
        else:
            chosen = next(iter(bricks), None)
        parts = bricks.get(chosen, {})

        missing = [name for ident, name in
                   [(_ID_RGB_LED, "RGB LED Bricklet 2.0"),
                    (_ID_COLOR, "Color Bricklet 2.0")] if ident not in parts]
        if missing:
            ipcon.disconnect()
            if master is not None:
                others = ", ".join(sorted(bricks)) or "none"
                raise RuntimeError(
                    f"Not found at master brick {master}: {', '.join(missing)}. "
                    f"Master bricks with bricklets: {others}.")
            raise RuntimeError(
                f"Not found: {', '.join(missing)}. Is the device plugged "
                f"into USB, and is the Brick Daemon running on "
                f"{host}:{port}?")

        # The upload companion hangs onto the log BEFORE the first event,
        # so that the starting configuration is uploaded as well.
        server = os.environ.get("LIFI_SERVER", server)
        telemetry = None
        if server and str(server).lower() not in ("off", "none", "0"):
            telemetry = Telemetry(server, parts[_ID_RGB_LED],
                                  parts[_ID_COLOR])
            log.sink = telemetry.record

        # Crashes end up in the log as the type of error plus a cleaned
        # message (details and limits: _telemetry.py, at the bottom)
        install_error_hook(log, telemetry)

        led = Led(BrickletRGBLEDV2(parts[_ID_RGB_LED], ipcon), log)
        sensor = Sensor(BrickletColorV2(parts[_ID_COLOR], ipcon), log)
        log.write("connect", led_uid=led.uid, sensor_uid=sensor.uid,
                  master_uid=chosen, version=__version__)
        return cls(ipcon, led, sensor, log, telemetry, master_uid=chosen)

    def close(self):
        """Switches the LED off and closes the connection cleanly."""
        try:
            self.led.off()
        finally:
            self._log.write("close")
            if self._telemetry is not None:
                self._telemetry.close()
            self._ipcon.disconnect()

    # ``with LifiDevice.connect() as lifi:`` cleans up by itself
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# --- The easy way in -----------------------------------------------------------
# ``from lifi_hardware import led, sensor`` is enough to start with: the two
# objects connect by themselves on their FIRST use and share one device.
# When the program ends, the LED goes off and the connection is closed. If
# you want more control (your own log, say, or two devices), use
# LifiDevice.connect(). Which device they use is decided by use_device() or
# the environment variable LIFI_MASTER; without either, it is the one device
# plugged into the computer.

_shared_device = None
_selected_master = os.environ.get("LIFI_MASTER") or None


def use_device(master_uid):
    """Chooses the device for ``led`` and ``sensor`` by the UID of its
    master brick, for example ``use_device("64SK5w")``. Call it before the
    first use of led or sensor."""
    global _selected_master
    if _shared_device is not None and _shared_device.master_uid != master_uid:
        raise RuntimeError(
            f"led and sensor are already connected to master brick "
            f"{_shared_device.master_uid}. Call use_device() before the "
            f"first use of led or sensor.")
    _selected_master = master_uid


def _device():
    global _shared_device
    if _shared_device is None:
        import atexit
        _shared_device = LifiDevice.connect(master=_selected_master)
        atexit.register(_shared_device.close)
    return _shared_device


class _LazyPart:
    """A placeholder that hooks itself to the real part on first access."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        return getattr(getattr(_device(), self._name), attr)


# For the editor, led and sensor are an Led and a Sensor, so typing "led."
# shows their methods with their descriptions. At run time they stay
# placeholders that connect only on their first use.
led = cast(Led, _LazyPart("led"))
sensor = cast(Sensor, _LazyPart("sensor"))
