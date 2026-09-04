"""Testskript fuer die Absturz-Ereignisse (lifi_hardware 0.3.0).

Loest nacheinander vier typische Fehler aus, jeden in einem eigenen
Unterprozess (ein Programm kann ja nur einmal abstuerzen). Im Cockpit
erscheinen sie als rote Marken, auf dem Server als error-Ereignisse:

    python error_demo.py            # alle vier Faelle nacheinander
    python error_demo.py name       # nur einen Fall ausloesen

Die Faelle:
    name    NameError       (Tippfehler im Variablennamen)
    type    TypeError       (Zahl plus Text)
    file    FileNotFoundError (Meldung enthaelt einen Pfad; im Upload
                               muss daraus <pfad> geworden sein)
    value   ValueError      (Bibliotheksfehler: unerlaubte Integrationszeit)
"""

import subprocess
import sys
import time

CASES = ["name", "type", "file", "value"]

if len(sys.argv) == 1:
    for case in CASES:
        print(f"--- Fall: {case}")
        subprocess.run([sys.executable, __file__, case])
        time.sleep(0.5)
    print()
    print("Vier Fehler ausgeloest. Nachsehen:")
    print("  Cockpit: https://lifi.uber.space/cockpit?team=pilot-01")
    print("  (rote Marken auf der Zeitleiste, Kachel 'last error')")
    sys.exit(0)

from lifi_hardware import LifiDevice

lifi = LifiDevice.connect(log_file=None)
case = sys.argv[1]

if case == "name":
    sensr.read()                          # noqa: F821, mit Absicht falsch
elif case == "type":
    ergebnis = 1 + "1"
elif case == "file":
    open(r"C:\gibtsnicht\messwerte.txt")
elif case == "value":
    lifi.sensor.set_integration_time(50)  # 50 ms gibt es nicht
else:
    print(f"Unbekannter Fall: {case}")
