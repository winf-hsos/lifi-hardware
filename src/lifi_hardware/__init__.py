"""lifi_hardware: die Schnittstelle zu eurem LiFi-Geraet.

Ein Geraet traegt eine RGB-LED (der Sender) und einen Farbsensor (der
Empfaenger), beide von Tinkerforge, verbunden ueber den Brick Daemon.
Dieses Modul findet die Bausteine beim Start selbst und gibt euch zwei
handliche Objekte: ``led`` und ``sensor``.

    from lifi_hardware import LifiDevice

    lifi = LifiDevice.connect()
    lifi.led.set_color(255, 0, 0)      # Rot senden
    reading = lifi.sensor.read()       # Reading(r, g, b, c)
    lifi.led.off()
    lifi.close()

Zwei Stellschrauben des Sensors sind mit Absicht sichtbar, denn sie
sind der zentrale Zielkonflikt des Projekts (Signalqualitaet gegen
Symbolrate): ``set_integration_time()`` und ``set_gain()``. Sie sind
nicht versteckt, nicht wegabstrahiert, und ihr sollt an ihnen drehen.

Alles, was das Geraet tut, landet zusaetzlich in einem lokalen
Messprotokoll (``lifi_log.jsonl`` im Arbeitsordner): eine Zeile JSON je
Ereignis. Das ist euer Messreihen-Gedaechtnis und funktioniert immer,
auch ganz ohne Netz.

Die rohe Tinkerforge-API bleibt zugaenglich (``lifi.led.raw`` und
``lifi.sensor.raw``), falls ihr tiefer wollt.
"""

from __future__ import annotations

import json
import time
from typing import NamedTuple

from tinkerforge.ip_connection import IPConnection
from tinkerforge.bricklet_color_v2 import BrickletColorV2
from tinkerforge.bricklet_rgb_led_v2 import BrickletRGBLEDV2

__version__ = "0.1.1"

# Geraetekennungen von Tinkerforge (device_identifier)
_ID_RGB_LED = 2127               # RGB LED Bricklet 2.0
_ID_COLOR = 2128                 # Color Bricklet 2.0

# Der Sensor kennt nur diese Integrationszeiten (Millisekunden) ...
_INTEGRATION_TIMES = {
    2.4: BrickletColorV2.INTEGRATION_TIME_2MS,
    24: BrickletColorV2.INTEGRATION_TIME_24MS,
    101: BrickletColorV2.INTEGRATION_TIME_101MS,
    154: BrickletColorV2.INTEGRATION_TIME_154MS,
    700: BrickletColorV2.INTEGRATION_TIME_700MS,
}
# ... und diese Verstaerkungsfaktoren.
_GAINS = {
    1: BrickletColorV2.GAIN_1X,
    4: BrickletColorV2.GAIN_4X,
    16: BrickletColorV2.GAIN_16X,
    60: BrickletColorV2.GAIN_60X,
}


class Reading(NamedTuple):
    """Eine Sensormessung: Rot, Gruen, Blau und Clear (ungefiltert).

    Alle vier Werte sind 16-Bit-Zahlen (0 bis 65535). Der Clear-Kanal
    misst ohne Farbfilter und ist deshalb der empfindlichste. Die Werte
    entsprechen NICHT den gesendeten RGB-Werten; dazwischen liegen
    ueberlappende Filter, Abstand und Umgebungslicht. Kalibrieren!
    """

    r: int
    g: int
    b: int
    c: int


class _Log:
    """Das lokale Messprotokoll: eine JSON-Zeile je Ereignis."""

    def __init__(self, path):
        self.path = path

    def write(self, event, **fields):
        if self.path is None:
            return
        record = {"t": round(time.time(), 4), "event": event, **fields}
        # Anhaengen statt offen halten: robust gegen Abstuerze, und
        # mehrere Programme koennen nacheinander ins selbe Protokoll.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


class Led:
    """Der Sender: eine RGB-LED mit drei Kanaelen von 0 bis 255."""

    def __init__(self, raw, log):
        self.raw = raw           # das rohe Tinkerforge-Objekt
        self._log = log
        self.uid = raw.uid_string

    def set_color(self, r, g, b):
        """Setzt die Farbe. Die LED bleibt an, bis etwas anderes kommt."""
        self.raw.set_rgb_value(r, g, b)
        self._log.write("set_color", r=r, g=g, b=b)

    def off(self):
        """Schaltet die LED aus (dasselbe wie set_color(0, 0, 0))."""
        self.set_color(0, 0, 0)


class Sensor:
    """Der Empfaenger: ein Farbsensor mit vier Kanaelen.

    Die zwei Stellschrauben: Eine laengere Integrationszeit sammelt
    mehr Licht je Messung (ruhigere Werte, aber weniger Messungen je
    Sekunde), eine hoehere Verstaerkung macht schwaches Licht sichtbar
    (aber auch das Rauschen). Beide gehoeren in euer Messprotokoll.
    """

    def __init__(self, raw, log):
        self.raw = raw
        self._log = log
        self.uid = raw.uid_string
        # Ein bekannter Startzustand, damit Messreihen vergleichbar
        # sind. Gain 16x, weil 60x in normal beleuchteten Raeumen den
        # Clear-Kanal schon ohne Signal saettigt (gemessen 03.09.2026).
        # Was fuer EURE Strecke passt, sagt nur eure eigene Messreihe.
        self._gain = 16
        self._integration_time = 154
        self._apply()
        # Die weisse Beleuchtungs-LED des Sensors ist zum FARBEN VON
        # OBJEKTEN gedacht und stoert unsere Strecke nur: aus damit.
        self.set_light(False)

    def _apply(self):
        self.raw.set_configuration(_GAINS[self._gain],
                                   _INTEGRATION_TIMES[self._integration_time])
        self._log.write("configure", gain=self._gain,
                        integration_time=self._integration_time)

    @property
    def integration_time(self):
        """Aktuelle Integrationszeit in Millisekunden."""
        return self._integration_time

    @property
    def gain(self):
        """Aktueller Verstaerkungsfaktor."""
        return self._gain

    def set_integration_time(self, ms):
        """Setzt die Integrationszeit. Erlaubt: 2.4, 24, 101, 154, 700."""
        if ms not in _INTEGRATION_TIMES:
            raise ValueError(f"Integrationszeit {ms} ms gibt es nicht. "
                             f"Erlaubt: {sorted(_INTEGRATION_TIMES)}")
        self._integration_time = ms
        self._apply()

    def set_gain(self, factor):
        """Setzt die Verstaerkung. Erlaubt: 1, 4, 16, 60."""
        if factor not in _GAINS:
            raise ValueError(f"Verstaerkung {factor}x gibt es nicht. "
                             f"Erlaubt: {sorted(_GAINS)}")
        self._gain = factor
        self._apply()

    def set_light(self, on):
        """Schaltet die weisse Beleuchtungs-LED des Sensors.

        Sie ist fuers Anleuchten von Objekten gedacht (der Sensor kann
        auch Oberflaechenfarben messen) und beim Start immer aus. Fuer
        die Lichtstrecke bleibt sie aus, sonst blendet der Empfaenger
        sich und sein Gegenueber selbst.
        """
        self.raw.set_light(bool(on))
        self._log.write("set_light", on=bool(on))

    def read(self):
        """Liest eine Messung und gibt sie als Reading(r, g, b, c) zurueck.

        Wichtig: Der Sensor misst im eigenen Takt, eine Messung je
        Integrationszeit. Dieser Aufruf holt die JUENGSTE fertige
        Messung ab und wartet nicht. Wer oefter liest, als der Sensor
        misst, bekommt denselben Wert mehrfach; neue Information gibt
        es hoechstens einmal je Integrationszeit. Genau deshalb ist die
        Integrationszeit eine Stellschraube: ruhigere Werte gegen
        weniger Messungen je Sekunde.
        """
        r, g, b, c = self.raw.get_color()
        reading = Reading(r, g, b, c)
        self._log.write("read", r=r, g=g, b=b, c=c)
        return reading


class LifiDevice:
    """Euer LiFi-Geraet: eine LED und ein Sensor an einem Brick."""

    def __init__(self, ipcon, led, sensor, log):
        self._ipcon = ipcon
        self.led = led
        self.sensor = sensor
        self._log = log

    @classmethod
    def connect(cls, host="localhost", port=4223, log_file="lifi_log.jsonl"):
        """Verbindet sich mit dem Brick Daemon und findet LED und Sensor.

        Es ist keine Konfiguration noetig: Die Bausteine melden sich
        beim Start mit ihrer eindeutigen Kennung (UID) und ihrem Typ.
        ``log_file=None`` schaltet das lokale Messprotokoll ab.
        """
        log = _Log(log_file)
        ipcon = IPConnection()
        ipcon.connect(host, port)

        found = {}

        def on_enumerate(uid, connected_uid, position, hw_version,
                         fw_version, device_identifier, enumeration_type):
            found.setdefault(device_identifier, uid)

        ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE, on_enumerate)
        ipcon.enumerate()
        # Kurz warten, bis sich alle Bausteine gemeldet haben
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if _ID_RGB_LED in found and _ID_COLOR in found:
                break
            time.sleep(0.05)

        missing = [name for ident, name in
                   [(_ID_RGB_LED, "RGB LED Bricklet 2.0"),
                    (_ID_COLOR, "Color Bricklet 2.0")] if ident not in found]
        if missing:
            ipcon.disconnect()
            raise RuntimeError(
                f"Nicht gefunden: {', '.join(missing)}. Steckt das Geraet am "
                f"USB-Port, und laeuft der Brick Daemon auf {host}:{port}?")

        led = Led(BrickletRGBLEDV2(found[_ID_RGB_LED], ipcon), log)
        sensor = Sensor(BrickletColorV2(found[_ID_COLOR], ipcon), log)
        log.write("connect", led_uid=led.uid, sensor_uid=sensor.uid,
                  version=__version__)
        return cls(ipcon, led, sensor, log)

    def close(self):
        """Schaltet die LED aus und trennt die Verbindung sauber."""
        try:
            self.led.off()
        finally:
            self._log.write("close")
            self._ipcon.disconnect()

    # ``with LifiDevice.connect() as lifi:`` raeumt automatisch auf
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# --- Der bequeme Einstieg ----------------------------------------------------
# ``from lifi_hardware import led, sensor`` reicht fuer den Anfang:
# Die beiden Objekte verbinden sich beim ERSTEN Gebrauch von selbst und
# teilen sich ein Geraet. Beim Programmende geht die LED automatisch
# aus und die Verbindung wird getrennt. Wer mehr Kontrolle will (etwa
# ein eigenes Protokoll oder zwei Geraete), nimmt LifiDevice.connect().

_shared_device = None


def _device():
    global _shared_device
    if _shared_device is None:
        import atexit
        _shared_device = LifiDevice.connect()
        atexit.register(_shared_device.close)
    return _shared_device


class _LazyPart:
    """Platzhalter, der sich beim ersten Zugriff ans echte Teil haengt."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        return getattr(getattr(_device(), self._name), attr)


led = _LazyPart("led")
sensor = _LazyPart("sensor")
