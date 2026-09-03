# lifi-hardware

Die lesbare Schnittstelle zu LED und Farbsensor des LiFi-Projekts an der Hochschule Osnabrück (Tinkerforge Master Brick, RGB LED Bricklet 2.0, Color Bricklet 2.0).

## Installieren

```
pip install git+https://github.com/winf-hsos/lifi-hardware.git
```

Voraussetzungen: Der Brick Daemon läuft, und das Gerät steckt am USB-Port.

## Benutzen

```python
from lifi_hardware import LifiDevice

lifi = LifiDevice.connect()

lifi.led.set_color(255, 0, 0)     # Rot senden
reading = lifi.sensor.read()      # Reading(r, g, b, c)
print(reading)

lifi.sensor.set_integration_time(24)   # 2.4, 24, 101, 154 oder 700 ms
lifi.sensor.set_gain(16)               # 1, 4, 16 oder 60

lifi.close()
```

Die zwei Stellschrauben des Sensors (Integrationszeit und Verstärkung) sind mit Absicht sichtbar: Sie sind der zentrale Zielkonflikt des Projekts, und ihr sollt an ihnen drehen. Alles, was das Gerät tut, landet zusätzlich als Messprotokoll in `lifi_log.jsonl` im Arbeitsordner (`log_file=None` schaltet das ab).

Zusätzlich geht das Messprotokoll an den Kursserver, damit ihr eure Strecke live im Browser seht und die Challenge-Tests laufen können. Hochgeladen wird exakt das, was in eurer lokalen Datei steht, nichts weiter; Messungen werden zu Paketen gebündelt. Abschalten könnt ihr das jederzeit und ohne Nachteil mit `LifiDevice.connect(server=None)` oder der Umgebungsvariable `LIFI_SERVER=off`. Ohne Netz oder Server funktioniert alles genauso, nur ohne Live-Ansicht. Einzelheiten stehen in `_telemetry.py`.

Das Modul ist bewusst kurz und kommentiert: Ab Challenge 2 lohnt es sich, hineinzuschauen. Die rohe Tinkerforge-API bleibt über `lifi.led.raw` und `lifi.sensor.raw` erreichbar.
