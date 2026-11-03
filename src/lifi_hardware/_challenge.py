"""Challenge 1: ``sender``, ``receiver`` and ``run_test``.

In Challenge 1 one device only sends and the other only receives. A team
writes one file with three functions and hands them to ``run_test``:

    from lifi_hardware import sender, receiver, run_test

    COLORS = [(0, 0, 0), (255, 255, 255)]

    def calibrate(readings):
        ...                                   # learn what each symbol looks like

    def send_symbol(index):
        sender.led.set_color(*COLORS[index])

    def receive_symbol():
        r, g, b, c = receiver.sensor.read()
        ...                                   # which symbol was it?
        return 0

    run_test(send_symbol, receive_symbol, calibrate, symbols=len(COLORS))

The same file works in both set-ups. With both devices on one laptop, the
module asks once which of the two sends, and the test runs step by step on
this laptop. With one device per laptop, the same file runs on both, the
module asks whether this laptop sends or receives, and the course server
keeps the two in step.

Why the server keeps time here: in Challenge 1 only one question counts,
how many symbols you can tell apart without a mistake. Timing does not
matter yet, so the server says when the next symbol comes. From Challenge 2
on, timing is your problem, and the server no longer helps.

The sequence of symbols comes fresh from the server for every run, so
nobody knows it in advance. What was sent and what was recognised is noted
by ``note_sent`` and ``note_received``; only ``run_test`` calls them, so
that your own code cannot decide what counts as sent. Without the server
(no WLAN, practising at home), a run with both devices on one laptop still
works, with a local random sequence, and is clearly marked as a practice
run that does not count.
"""

from __future__ import annotations

import atexit
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from typing import cast

from . import LifiDevice, find_devices
from ._telemetry import DEFAULT_SERVER

_READINGS_PER_SYMBOL = 5     # readings per symbol during calibration
_POLL = 0.15                 # seconds between two questions to the server
_WAIT_FOR_PARTNER = 300      # seconds to wait for the other laptop
_TIMEOUT = 5                 # seconds for one request to the server


# --- Colours on the console -------------------------------------------------------
#
# Only for looks: ok in green, mistakes in red, and a small block in the colour
# the LED showed. When the output does not go to a console (for example when
# OpenCode reads it) or NO_COLOR is set, everything stays plain text.

def _colour_support():
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not getattr(sys.stdout, "isatty", lambda: False)():
        return False
    if os.name == "nt":
        # A Windows console understands colour codes only after being asked
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
        except Exception:
            return False
    return True


_COLOUR = _colour_support()
_STYLES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
           "cyan": "36", "on-green": "42", "on-red": "41"}


def _paint(text, *styles):
    """The text in the given styles, or unchanged without colour support."""
    if not _COLOUR or not styles:
        return text
    return f"\033[{';'.join(_STYLES[s] for s in styles)}m{text}\033[0m"


def _swatch(rgb):
    """Two character cells in the colour (r, g, b), for the console.

    Uses the 256-colour palette, which every common console shows. Off is
    drawn as two dim dashes, because black would vanish on a black console.
    """
    if not _COLOUR or rgb is None:
        return ""
    if tuple(rgb) == (0, 0, 0):
        return _paint("--", "dim") + " "
    r, g, b = (round(v / 255 * 5) for v in rgb)
    return f"\033[48;5;{16 + 36 * r + 6 * g + b}m  \033[0m "


def _heading(title, text, end=""):
    """A step of the test, such as "Calibrating: ...". Without a line break,
    so that the calibration can add one mark per symbol to the same line."""
    print(f"{_paint(title, 'cyan', 'bold')}: {text}", end=end, flush=True)


def _show(symbol):
    """A recognised symbol for printing; None (an invalid answer) as a dash."""
    return "-" if symbol is None else str(symbol)


def _shown_colour():
    """The colour the sending LED shows right now, if it is on this laptop."""
    if not _COLOUR or not _roles or _roles["sender"] is None:
        return None
    try:
        return _roles["sender"].led.color
    except Exception:
        return None


# --- Who sends, who receives ----------------------------------------------------

_roles = None                # {"sender": LifiDevice or None, "receiver": ...}


def _ask(question, answers):
    """Asks until one of the allowed answers comes back."""
    while True:
        answer = input(question).strip().lower()
        if answer in answers:
            return answer
        print(f"Please answer with {' or '.join(answers)}.")


def _choose_roles():
    """Finds the devices on this laptop and asks which one does what."""
    global _roles
    if _roles is not None:
        return _roles
    masters = find_devices()
    if not masters:
        raise RuntimeError(
            "No device found. Is it plugged into USB, and is the Brick "
            "Daemon running?")

    if len(masters) == 1:
        device = LifiDevice.connect(master=masters[0])
        atexit.register(device.close)
        print(f"One device on this laptop ({masters[0]}).")
        answer = _ask("Does this laptop send (s) or receive (r)? ", ["s", "r"])
        _roles = ({"sender": device, "receiver": None} if answer == "s"
                  else {"sender": None, "receiver": device})
        return _roles

    # Two or more devices: each gets its own measurement log, otherwise
    # the readings of both would end up mixed in one file.
    devices = {m: LifiDevice.connect(master=m, log_file=f"lifi_log_{m}.jsonl")
               for m in masters}
    for device in devices.values():
        atexit.register(device.close)
    if len(masters) == 2:
        first, second = masters
        devices[first].led.set_color(255, 255, 255)
        print(f"Two devices on this laptop: {first} and {second}.")
        answer = _ask("The device whose LED is on now: does it send (s) "
                      "or receive (r)? ", ["s", "r"])
        devices[first].led.off()
        sending, receiving = (first, second) if answer == "s" else (second, first)
    else:
        print("Devices on this laptop:")
        for number, master in enumerate(masters, start=1):
            print(f"  {number}  {master}")
        numbers = [str(n) for n in range(1, len(masters) + 1)]
        sending = masters[int(_ask("Number of the device that sends: ", numbers)) - 1]
        receiving = masters[int(_ask("Number of the device that receives: ", numbers)) - 1]
        if sending == receiving:
            raise RuntimeError("Sender and receiver have to be two different devices.")
    print(f"Sender: {_paint(sending, 'bold')}, receiver: {_paint(receiving, 'bold')}")
    _roles = {"sender": devices[sending], "receiver": devices[receiving]}
    return _roles


class _Role:
    """``sender`` or ``receiver``: the device in that role, found on first use."""

    def __init__(self, name):
        self._name = name

    def _device(self):
        device = _choose_roles()[self._name]
        if device is None:
            other = "receiver" if self._name == "sender" else "sender"
            raise RuntimeError(
                f"This laptop is the {other}. The {self._name} is the device "
                f"on the other laptop, so {self._name}.led and "
                f"{self._name}.sensor do not exist here.")
        return device

    def __getattr__(self, attr):
        return getattr(self._device(), attr)


# For the editor, sender and receiver are LifiDevices, so "sender." offers
# led and sensor. At run time they are placeholders that decide on first use.
sender = cast(LifiDevice, _Role("sender"))
receiver = cast(LifiDevice, _Role("receiver"))


# --- Talking to the server ------------------------------------------------------

class ServerError(RuntimeError):
    """The server answered, but with a refusal (the reason is the message)."""


def _server():
    url = os.environ.get("LIFI_SERVER", DEFAULT_SERVER)
    if not url or str(url).lower() in ("off", "none", "0"):
        return None
    return url.rstrip("/")


def _api(method, path, body=None):
    request = urllib.request.Request(
        _server() + path, method=method,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise ServerError(detail or f"the server answered {error.code}") from None


# --- Noting what was sent and received ------------------------------------------

class _Run:
    """One run of run_test: on the server, or locally as a practice run."""

    def __init__(self, role, symbols, runs, practice, run_id=None):
        self.role = role
        self.symbols = symbols
        self.runs = runs
        self.practice = practice
        self.id = run_id
        self.sequence = None         # known locally only in a practice run
        self.received = []


def note_sent(run, position, symbol):
    """Notes that ``symbol`` now shows at ``position`` of the run.

    Called by run_test only, never by your own code: what counts as sent
    is decided by the test, not by the program being tested. The note goes
    into the measurement log of the sending device and to the server.
    """
    device = _roles["sender"]
    device._log.write("note_sent", run=run.id, position=position, symbol=symbol)
    if not run.practice:
        return _api("POST", f"/api/runs/{run.id}/sent",
                    {"role": run.role, "position": position, "symbol": symbol})


def note_received(run, position, symbol):
    """Notes which symbol the receiver recognised at ``position``.

    Called by run_test only. During calibration ``symbol`` is None. The note
    goes into the measurement log of the receiving device and to the server.
    """
    device = _roles["receiver"]
    device._log.write("note_received", run=run.id, position=position,
                      symbol=symbol)
    if not run.practice:
        return _api("POST", f"/api/runs/{run.id}/received",
                    {"role": run.role, "position": position, "symbol": symbol})


# --- The test -------------------------------------------------------------------

def _settle_time():
    """How long a new symbol has to stand before a reading shows it.

    The sensor hands out its newest FINISHED reading. Right after a change,
    that reading may still have begun before it, so we wait for two full
    integration times.
    """
    return 2 * receiver.sensor.integration_time / 1000 + 0.05


def _measure_calibration():
    """A few readings of the symbol that is showing right now."""
    pause = receiver.sensor.integration_time / 1000
    readings = []
    for _ in range(_READINGS_PER_SYMBOL):
        readings.append(receiver.sensor.read())
        time.sleep(pause)
    return readings


def _check(symbol, symbols):
    if isinstance(symbol, bool) or not isinstance(symbol, int) or not 0 <= symbol < symbols:
        print(_paint(f"  receive_symbol() returned {symbol!r}, but it has to return a "
                     f"whole number from 0 to {symbols - 1}. Counted as a mistake.",
                     "yellow"))
        return None
    return symbol


def _wait(run, until, what):
    """Asks the server until ``until(state)`` holds and returns that state."""
    started = time.time()
    shown = False
    while True:
        state = _api("GET", f"/api/runs/{run.id}?role={run.role}")
        if until(state):
            return state
        if not shown and time.time() - started > 2:
            print(_paint(f"  waiting for {what} ...", "dim"))
            shown = True
        if time.time() - started > _WAIT_FOR_PARTNER:
            raise RuntimeError(f"Waited {_WAIT_FOR_PARTNER} s for {what}. "
                               f"Start run_test again on both laptops.")
        time.sleep(_POLL)


def run_test(send_symbol, receive_symbol, calibrate=None, symbols=None,
             runs=20, team=None):
    """The test for Challenge 1: how many symbols get through without a mistake?

    ``send_symbol(index)`` shows the symbol with that number, from 0 to
    ``symbols - 1``. ``receive_symbol()`` measures and returns the number of
    the symbol it recognised. ``calibrate(readings)`` is called once before
    the test, after the whole alphabet has been shown once: ``readings`` is a
    dictionary from each symbol number to a list of Readings taken while that
    symbol was showing. What you make of them is up to you.

    ``symbols`` is the size of your alphabet, usually ``len(COLORS)``. ``team``
    is needed only if your devices are not registered with a team yet: then
    both laptops find each other by this name, and the run does not count.

    Returns a dictionary with the number of hits and the two sequences.
    """
    if symbols is None:
        raise TypeError("run_test needs the size of your alphabet, for "
                        "example run_test(..., symbols=len(COLORS))")
    roles = _choose_roles()
    role = ("both" if roles["sender"] and roles["receiver"]
            else "sender" if roles["sender"] else "receiver")
    some_device = roles["sender"] or roles["receiver"]

    run = _start(role, symbols, runs, team, some_device)
    try:
        if role == "both":
            _run_both(run, send_symbol, receive_symbol, calibrate)
        elif role == "sender":
            _run_sender(run, send_symbol)
        else:
            _run_receiver(run, receive_symbol, calibrate)
    finally:
        if roles["sender"] is not None:
            roles["sender"].led.off()
    return _report(run)


def _start(role, symbols, runs, team, device):
    """Asks the server for a new run, or falls back to a practice run."""
    if _server() is not None:
        try:
            state = _api("POST", "/api/runs/join", {
                "role": role, "led_uid": device.led.uid,
                "sensor_uid": device.sensor.uid,
                "master_uid": device.master_uid, "team": team,
                "symbols": symbols, "runs": runs})
            run = _Run(role, symbols, runs, practice=False, run_id=state["run_id"])
            note = (_paint("counts", "green") if state["counted"] else
                    _paint("does NOT count (devices not registered)", "yellow"))
            print(f"Run for {_paint(state['team'], 'bold')}: {symbols} symbols, "
                  f"{runs} runs, this run {note}.")
            return run
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
    if role != "both":
        raise RuntimeError(
            "The course server cannot be reached. With two laptops, the server "
            "keeps the two in step, so this does not work offline. Check the "
            "WLAN, or plug both devices into one laptop for a practice run.")
    print(_paint("The course server cannot be reached: PRACTICE RUN, it does not count.",
                 "yellow"))
    run = _Run(role, symbols, runs, practice=True, run_id="practice")
    rng = random.SystemRandom()
    run.sequence = [rng.randrange(symbols) for _ in range(runs)]
    return run


def _steps(run):
    """The steps of a practice run, in the same order as on the server."""
    return ([("calibrate", i) for i in range(run.symbols)]
            + [("test", s) for s in run.sequence])


def _run_both(run, send_symbol, receive_symbol, calibrate):
    """Both devices on this laptop: send, wait, measure, note, next."""
    _heading("Calibrating", "showing the whole alphabet once ")
    readings = {}
    position = 0
    while True:
        if run.practice:
            if position >= run.symbols + run.runs:
                return
            phase, symbol = _steps(run)[position]
        else:
            state = _api("GET", f"/api/runs/{run.id}?role=both")
            if state["state"] == "done":
                run.state = state
                return
            phase, symbol = state["phase"], state["symbol"]
        if phase == "test" and position == run.symbols:
            print()
            if calibrate is not None:
                calibrate(readings)
            _heading("Testing", f"{run.runs} symbols", end="\n")
        send_symbol(symbol)
        time.sleep(_settle_time())
        note_sent(run, position, symbol)
        if phase == "calibrate":
            readings[symbol] = _measure_calibration()
            note_received(run, position, None)
            print(_swatch(_shown_colour()) or ".", end="", flush=True)
        else:
            got = _check(receive_symbol(), run.symbols)
            run.received.append(got)
            note_received(run, position, got)
            mark = _paint("ok", "green") if got == symbol else _paint("MISTAKE", "red", "bold")
            print(f"  {position - run.symbols + 1:>3}/{run.runs}  sent {symbol:>2} "
                  f"{_swatch(_shown_colour())}recognised {_show(got):>2}  {mark}")
        position += 1


def _run_sender(run, send_symbol):
    """This laptop only sends; the server says what and when."""
    _wait(run, lambda s: s["state"] != "waiting", "the receiving laptop")
    _heading("Calibrating", "showing the whole alphabet once ")
    while True:
        state = _wait(run, lambda s: s["state"] == "done" or not s["sent"],
                      "the receiver")
        if state["state"] == "done":
            run.state = state
            return
        position, symbol = state["position"], state["symbol"]
        if state["phase"] == "test" and position == run.symbols:
            print()
            _heading("Testing", f"{run.runs} symbols", end="\n")
        send_symbol(symbol)
        time.sleep(0.1)
        note_sent(run, position, symbol)
        if state["phase"] == "test":
            print(f"  {position - run.symbols + 1:>3}/{run.runs}  sent {symbol:>2} "
                  f"{_swatch(_shown_colour())}".rstrip())
        else:
            print(_swatch(_shown_colour()) or ".", end="", flush=True)
        # Wait until the receiver has measured, then the next symbol comes
        _wait(run, lambda s: s["state"] == "done" or s["position"] > position,
              "the receiver")


def _run_receiver(run, receive_symbol, calibrate):
    """This laptop only receives; it measures whenever the sender is ready."""
    _wait(run, lambda s: s["state"] != "waiting", "the sending laptop")
    _heading("Calibrating", "measuring the whole alphabet once ")
    readings = {}
    while True:
        state = _wait(run, lambda s: s["state"] == "done" or s["sent"],
                      "the sender")
        if state["state"] == "done":
            run.state = state
            return
        position = state["position"]
        time.sleep(_settle_time())
        if state["phase"] == "calibrate":
            readings[state["symbol"]] = _measure_calibration()
            note_received(run, position, None)
            print(".", end="", flush=True)
            continue
        if position == run.symbols:
            print()
            if calibrate is not None:
                calibrate(readings)
            _heading("Testing", f"{run.runs} symbols", end="\n")
        got = _check(receive_symbol(), run.symbols)
        run.received.append(got)
        result = note_received(run, position, got)
        print(f"  {position - run.symbols + 1:>3}/{run.runs}  recognised {_show(got):>2}")
        if result and result["state"] == "done":
            run.state = result
            return


def _report(run):
    """Prints the result and where the mistakes were."""
    if run.practice:
        sequence, received = run.sequence, run.received
    else:
        state = getattr(run, "state", None) or _api(
            "GET", f"/api/runs/{run.id}?role={run.role}")
        sequence, received = state["sequence"], state["received"]
    hits = sum(1 for a, b in zip(sequence, received) if a == b)
    print()
    # One cell per attempt: green for a hit, red for a mistake (+ and x
    # without colour support)
    if _COLOUR:
        hit, miss = _paint(" ", "on-green"), _paint(" ", "on-red")
    else:
        hit, miss = "+", "x"
    cells = " ".join(hit if a == b else miss for a, b in zip(sequence, received))
    print(f"  {cells}")
    kind = "Practice run" if run.practice else "Result"
    perfect = hits == run.runs
    print(_paint(f"{kind}: {hits} of {run.runs} recognised with {run.symbols} symbols.",
                 "bold", "green" if perfect else "red"))
    mistakes = [(i + 1, a, b) for i, (a, b) in enumerate(zip(sequence, received))
                if a != b]
    if mistakes:
        print("Mistakes (position: sent -> recognised):")
        for position, sent, got in mistakes:
            print(f"  {position:>3}: {sent} -> {_paint(_show(got), 'red')}")
        pairs = {}
        for _, sent, got in mistakes:
            pairs[(sent, got)] = pairs.get((sent, got), 0) + 1
        common = max(pairs.items(), key=lambda item: item[1])
        if common[1] > 1:
            (sent, got), count = common
            print(_paint(f"{sent} was taken for {_show(got)} {count} times: these two "
                         f"are probably too close together.", "yellow"))
    else:
        print(_paint("No mistakes.", "green"))
    if run.practice:
        print(_paint("This was a practice run; it does not count.", "yellow"))
    return {"hits": hits, "runs": run.runs, "symbols": run.symbols,
            "sequence": sequence, "received": received,
            "practice": run.practice}
