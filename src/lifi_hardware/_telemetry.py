"""The upload companion: sends the measurement log to the course server.

What is uploaded here is EXACTLY what is also in your local file
``lifi_log.jsonl``, not a line more. The server assigns the events to
your team through the UIDs of the device; that is how you see your own
link live in the team cockpit, and how the challenge acceptance tests
run on the projector.

Two hard rules:

1. The upload never touches the timing. Your program does not wait for
   the network: events go into a queue, and a background thread sends
   them in bundles. If the network is gone or the queue is full, events
   are dropped instead of waited for; the local file has everything.
2. Everything works without the server. Switch it off at any time with
   ``LifiDevice.connect(server=None)`` or the environment variable
   ``LIFI_SERVER=off``; it costs you nothing.

Sensor readings (``read``) are bundled into packets before sending,
because a tight measuring loop can produce thousands of them per minute;
the rare control events go one by one.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.request
from collections import deque

DEFAULT_SERVER = "https://lifi.uber.space"

_FLUSH_SECONDS = 2.0             # send at least this often
_MAX_QUEUE = 5000                # older events drop out at the front
_MAX_BATCH = 800                 # events per HTTP request
_MAX_SAMPLES = 500               # readings per read packet
_TIMEOUT = 3.0                   # seconds per HTTP request


class Telemetry:
    """A queue plus a background thread for the batch upload."""

    def __init__(self, server, led_uid, sensor_uid):
        self.url = server.rstrip("/") + "/api/events"
        self.led_uid = led_uid
        self.sensor_uid = sensor_uid
        # A deque with maxlen drops the oldest entries by itself when it
        # overflows, without ever blocking (rule 1)
        self._queue = deque(maxlen=_MAX_QUEUE)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="lifi-telemetry")
        self._thread.start()

    # -- Input (from the measurement log, must be lightning fast) ------------
    def record(self, record):
        self._queue.append(record)
        if len(self._queue) >= _MAX_BATCH:
            self._wake.set()

    # -- Background ----------------------------------------------------------
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
                # Drop instead of waiting: the local file has everything
                pass

    @staticmethod
    def _pack(events):
        """Bundles consecutive read events into packets.

        Many ``{"event": "read", r, g, b, c}`` become one
        ``{"event": "reads", "n": ..., "samples": [[t, r, g, b, c], ...]}``.
        Everything else stays unchanged and in its order.
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
        """Sends right away, for the moment of a crash (error event)."""
        self._flush()

    # -- End -----------------------------------------------------------------
    def close(self):
        """Sends the rest and ends the thread, with a short timeout."""
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=_TIMEOUT)
        self._flush()


# --- Crash events -------------------------------------------------------------
# When a program crashes, the error is logged as an event: the type of the
# exception and the CLEANED message. Cleaned means that all file paths are
# removed (they can contain user names), no traceback or source lines go
# along, and the length is capped. Ctrl+C (KeyboardInterrupt) and SystemExit
# do not count as errors. Decided on 2026-09-03; the course announces this
# collection on a slide.

# [\\/]+ instead of [\\/]: error messages often show paths through repr(),
# with DOUBLED backslashes (r"C:\\Users\\...").
_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:)?[\\/]+(?:[^\\/\s'\"]+[\\/]+)+[^\\/\s'\"]+")


def sanitize_error(message):
    """Removes paths from an error message and caps its length."""
    cleaned = _PATH_PATTERN.sub("<path>", str(message))
    return cleaned[:200]


def install_error_hook(log, telemetry):
    """Hooks into sys.excepthook without changing the normal output."""
    previous = sys.excepthook

    def hook(exc_type, exc, traceback):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            try:
                log.write("error", type=exc_type.__name__,
                          message=sanitize_error(exc))
                if telemetry is not None:
                    telemetry.flush_now()   # the thread is about to die too
            except Exception:
                pass                        # never make the crash worse
        previous(exc_type, exc, traceback)

    sys.excepthook = hook
