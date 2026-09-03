"""Pilotprogramm fuers Team-Cockpit: Farben senden, dabei messen.

Startet man dieses Skript mit angeschlossenem Geraet, laesst sich im
Cockpit live zuschauen (Team laut Registry, im Pilotbetrieb):

    https://lifi.uber.space/cockpit?team=pilot-01

Das Programm laeuft etwa eine Minute: zwei Runden durch acht Farben,
je drei Sekunden mit laufenden Messungen, und nach der ersten Runde
ein Wechsel von Integrationszeit und Verstaerkung, damit auch die
Konfigurationsmarke im Cockpit zu sehen ist.

    python cockpit_demo.py
"""

import time

from lifi_hardware import LifiDevice

COLORS = [
    (255, 0, 0), (255, 200, 0), (0, 255, 0), (0, 200, 255),
    (0, 0, 255), (255, 0, 255), (255, 255, 255), (0, 0, 0),
]

with LifiDevice.connect() as lifi:
    print(f"verbunden: LED {lifi.led.uid}, Sensor {lifi.sensor.uid}")
    print("Cockpit: https://lifi.uber.space/cockpit?team=pilot-01")
    for round_number in range(2):
        for rgb in COLORS:
            lifi.led.set_color(*rgb)
            until = time.time() + 3.0
            while time.time() < until:
                lifi.sensor.read()
                time.sleep(0.15)
        if round_number == 0:
            # Sichtbar im Cockpit als gestrichelte Marke
            lifi.sensor.set_integration_time(24)
            lifi.sensor.set_gain(4)

print("fertig — im Cockpit auf '2 min' stellen, dann ist alles zu sehen")
