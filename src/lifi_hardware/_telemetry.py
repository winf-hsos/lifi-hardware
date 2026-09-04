"""Der Upload-Begleiter: schickt das Messprotokoll an den Kursserver.

Was hier hochgeht, ist EXAKT das, was auch in eurer lokalen Datei
``lifi_log.jsonl`` steht, keine Zeile mehr. Der Server ordnet die
Ereignisse ueber die Geraete-UIDs eurem Team zu; damit seht ihr eure
eigene Strecke live im Team-Cockpit, und die Challenge-Abnahmen laufen
auf dem Beamer.

Zwei harte Regeln:

1. Der Upload fasst das Timing nie an. Euer Programm wartet nicht auf
   das Netz: Ereignisse wandern in eine Warteschlange, ein Hintergrund-
   Thread schickt sie gebuendelt. Ist das Netz weg oder die Schlange
   voll, wird verworfen statt gewartet; die lokale Datei hat alles.
2. Ohne Server funktioniert alles. Abschalten geht jederzeit mit
   ``LifiDevice.connect(server=None)`` oder der Umgebungsvariable
   ``LIFI_SERVER=off``, und es entsteht euch kein Nachteil daraus.

Sensor-Messungen (``read``) werden vor dem Senden zu Paketen
zusammengefasst, weil in einer engen Messschleife tausende je Minute
anfallen koennen; die seltenen Steuer-Ereignisse gehen einzeln.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.request
from collections import deque

DEFAULT_SERVER = "https://lifi.uber.space"

_FLUSH_SECONDS = 2.0             # spaetestens so oft wird gesendet
_MAX_QUEUE = 5000                # aeltere Ereignisse fallen vorne raus
_MAX_BATCH = 800                 # Ereignisse je HTTP-Anfrage
_MAX_SAMPLES = 500               # Messungen je read-Paket
_TIMEOUT = 3.0                   # Sekunden je HTTP-Anfrage


class Telemetry:
    """Warteschlange plus Hintergrund-Thread fuer den Batch-Upload."""

    def __init__(self, server, led_uid, sensor_uid):
        self.url = server.rstrip("/") + "/api/events"
        self.led_uid = led_uid
        self.sensor_uid = sensor_uid
        # deque mit maxlen verwirft bei Ueberlauf automatisch die
        # aeltesten Eintraege, ohne je zu blockieren (Regel 1)
        self._queue = deque(maxlen=_MAX_QUEUE)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="lifi-telemetry")
        self._thread.start()

    # -- Eingang (aus dem Messprotokoll, muss blitzschnell sein) -------------
    def record(self, record):
        self._queue.append(record)
        if len(self._queue) >= _MAX_BATCH:
            self._wake.set()

    # -- Hintergrund ---------------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(_FLUSH_SECONDS)
            self._wake.clear()
            self._flush()

    def _flush(self):
        with self._send_lock:
            events = []
            while self._queue and len(events) < _MAX_BATCH:
                events.append(self._queue.popleft())
            if not events:
                return
            body = json.dumps({
                "led_uid": self.led_uid,
                "sensor_uid": self.sensor_uid,
                "events": self._pack(events),
            }).encode("utf-8")
            request = urllib.request.Request(
                self.url, data=body,
                headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(request, timeout=_TIMEOUT).close()
            except Exception:
                # Verwerfen statt warten: die lokale Datei hat alles
                pass

    @staticmethod
    def _pack(events):
        """Fasst aufeinanderfolgende read-Ereignisse zu Paketen zusammen.

        Aus vielen ``{"event": "read", r, g, b, c}`` wird ein
        ``{"event": "reads", "n": ..., "samples": [[t, r, g, b, c], ...]}``.
        Alles andere bleibt unveraendert in seiner Reihenfolge.
        """
        packed = []
        run = []

        def close_run():
            if not run:
                return
            if len(run) == 1:
                packed.append(run[0])
            else:
                packed.append({
                    "t": run[0]["t"], "event": "reads", "n": len(run),
                    "samples": [[e["t"], e["r"], e["g"], e["b"], e["c"]]
                                for e in run],
                })
            run.clear()

        for event in events:
            if event.get("event") == "read":
                run.append(event)
                if len(run) >= _MAX_SAMPLES:
                    close_run()
            else:
                close_run()
                packed.append(event)
        close_run()
        return packed

    def flush_now(self):
        """Sofort senden, fuer den Absturzmoment (Fehler-Ereignis)."""
        self._flush()

    # -- Ende ----------------------------------------------------------------
    def close(self):
        """Schickt den Rest und beendet den Thread, mit kurzem Timeout."""
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=_TIMEOUT)
        self._flush()


# --- Absturz-Ereignisse ------------------------------------------------------
# Stuerzt ein Programm ab, wird der Fehler als Ereignis protokolliert:
# der Ausnahme-Typ und die BEREINIGTE Meldung. Bereinigt heisst: alle
# Dateipfade werden entfernt (sie koennen Benutzernamen enthalten), es
# gehen keine Traceback- oder Quelltextzeilen mit, und die Laenge ist
# gedeckelt. Strg+C (KeyboardInterrupt) und SystemExit zaehlen nicht
# als Fehler. Entschieden am 03.09.2026; die Erhebung steht auf der
# Ankuendigungsfolie des Kurses.

# [\\/]+ statt [\\/]: In Fehlermeldungen stehen Pfade oft per repr()
# mit VERDOPPELTEN Backslashes (r"C:\\Users\\...").
_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:)?[\\/]+(?:[^\\/\s'\"]+[\\/]+)+[^\\/\s'\"]+")


def sanitize_error(message):
    """Entfernt Pfade aus einer Fehlermeldung und deckelt die Laenge."""
    cleaned = _PATH_PATTERN.sub("<pfad>", str(message))
    return cleaned[:200]


def install_error_hook(log, telemetry):
    """Haengt sich an sys.excepthook, ohne die normale Ausgabe zu aendern."""
    previous = sys.excepthook

    def hook(exc_type, exc, traceback):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            try:
                log.write("error", type=exc_type.__name__,
                          message=sanitize_error(exc))
                if telemetry is not None:
                    telemetry.flush_now()   # der Thread stirbt gleich mit
            except Exception:
                pass                        # niemals den Absturz verschlimmern
        previous(exc_type, exc, traceback)

    sys.excepthook = hook
