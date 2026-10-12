"""Pilot program for the team cockpit: send colours and measure meanwhile.

Run this script with a device plugged in and you can watch it live in the
cockpit (team as in the registry, during the pilot):

    https://lifi.uber.space/cockpit?team=pilot-01

The program runs for about a minute: two rounds through eight colours,
three seconds each with readings running, and after the first round a
change of integration time and gain, so that the configuration mark shows
up in the cockpit as well.

    python cockpit_demo.py
"""

import time

from lifi_hardware import LifiDevice

COLORS = [
    (255, 0, 0), (255, 200, 0), (0, 255, 0), (0, 200, 255),
    (0, 0, 255), (255, 0, 255), (255, 255, 255), (0, 0, 0),
]

with LifiDevice.connect() as lifi:
    print(f"connected: LED {lifi.led.uid}, sensor {lifi.sensor.uid}")
    print("Cockpit: https://lifi.uber.space/cockpit?team=pilot-01")
    for round_number in range(2):
        for rgb in COLORS:
            lifi.led.set_color(*rgb)
            until = time.time() + 3.0
            while time.time() < until:
                lifi.sensor.read()
                time.sleep(0.15)
        if round_number == 0:
            # Shows up in the cockpit as a dashed mark
            lifi.sensor.set_integration_time(24)
            lifi.sensor.set_gain(4)

print("done: set the cockpit to '2 min' to see everything")
