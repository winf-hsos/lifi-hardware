"""Challenge 1: ``sender``, ``receiver`` and ``run_test``.

In Challenge 1 one device only sends and the other only receives. A team
writes one file with three functions and hands them to ``run_test``:

    from lifi_hardware import sender, receiver, run_test

    COLORS = {"off": (0, 0, 0), "white": (255, 255, 255)}

    def calibrate(readings):
        ...                                   # learn what each symbol looks like

    def send_symbol(name):
        sender.led.set_color(*COLORS[name])

    def receive_symbol():
        r, g, b, c = receiver.sensor.read()
        ...                                   # which symbol was it?
        return "off"

    run_test(send_symbol, receive_symbol, calibrate, symbols=COLORS)

The sensor of the receiving device measures with gain 16x and an
integration time of 154 ms unless you say otherwise. To try other
settings, hand them to run_test (since 0.7.0):

    run_test(send_symbol, receive_symbol, calibrate, symbols=COLORS,
             gain=4, integration_time=101)

run_test sets them on the receiving device before the calibration, so the
calibration and the test measure the same way; on a laptop that only sends,
they are ignored. Every run reports them to the server, and the team cockpit
shows them next to the result. Setting them inside calibrate or
receive_symbol would be too late: the calibration readings are taken before
calibrate is called.

The symbols are the names in COLORS: ``send_symbol`` gets a name,
``calibrate`` gets the readings by name, ``receive_symbol`` returns a name,
and the result names them too. On the way to the server they travel as
numbers (the position in COLORS), so names may be anything you like.
``symbols=len(COLORS)`` still works and numbers the symbols from 0.

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
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import cast

from . import _GAINS, _INTEGRATION_TIMES, LifiDevice, find_devices
from ._telemetry import DEFAULT_SERVER

_READINGS_PER_SYMBOL = 5     # readings per symbol during calibration
_POLL = 0.15                 # seconds between two questions to the server
_WAIT_FOR_PARTNER = 300      # seconds to wait for the other laptop
_TIMEOUT = 5                 # seconds for one request to the server
_RETRY_FOR = 30              # seconds to keep asking again after a failure
_RETRY_CODES = (502, 503, 504)  # the server is restarting or overloaded


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


def _show(run, index, width=0):
    """A symbol for printing, by its name; None (an invalid answer) as a dash.
    ``width`` pads to the longest name, so that the columns stay in line."""
    text = "-" if index is None else str(run.labels[index])
    return text.rjust(width)


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


def _api_once(method, path, body):
    request = urllib.request.Request(
        _server() + path, method=method,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _api(method, path, body=None):
    """One request to the course server, asked again after a short failure.

    In a room with forty laptops in one WLAN, a request now and then gets no
    answer in time. Since 0.8.1 that no longer ends the run: the module
    waits a moment and asks again, for up to ``_RETRY_FOR`` seconds. Asking
    twice is safe, because the server recognises a repeated message (the
    same position, the same ``join_id``) and does not count it twice.
    A refusal (the server answered, but said no) is not asked again.
    """
    started = time.time()
    pause = 0.5
    told = False
    while True:
        try:
            return _api_once(method, path, body)
        except urllib.error.HTTPError as error:
            if error.code not in _RETRY_CODES:
                try:
                    detail = json.loads(error.read().decode("utf-8")).get("detail")
                except (ValueError, AttributeError):
                    detail = None
                raise ServerError(detail or f"the server answered {error.code}") from None
            failure = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            failure = error
        if time.time() - started + pause > _RETRY_FOR:
            if isinstance(failure, urllib.error.HTTPError):
                raise ServerError(f"the server answered {failure.code}") from None
            raise failure
        if not told:
            print(_paint("  the course server did not answer in time, asking again ...",
                         "dim"))
            told = True
        time.sleep(pause)
        pause = min(pause * 2, 4)


# --- Noting what was sent and received ------------------------------------------

class _Run:
    """One run of run_test: on the server, or locally as a practice run.

    Inside, every symbol is its number (its position in the alphabet), and
    so it travels to the server. ``labels`` are the names your functions
    see: the names in COLORS, or 0, 1, 2, ... with ``symbols=len(COLORS)``.
    """

    def __init__(self, role, labels, runs, practice, run_id=None):
        self.role = role
        self.labels = labels
        self.symbols = len(labels)
        self.width = max(len(str(label)) for label in labels)
        self.runs = runs
        self.practice = practice
        self.id = run_id
        self.sequence = None         # known locally only in a practice run
        self.received = []


def _labels(symbols):
    """The names of the symbols: a number gives 0 to n-1, a dictionary such
    as COLORS gives its keys, a list gives its entries."""
    if isinstance(symbols, bool):
        raise TypeError("symbols has to be your alphabet, for example "
                        "run_test(..., symbols=COLORS)")
    if isinstance(symbols, int):
        labels = list(range(symbols))
    else:
        try:
            labels = list(symbols)
        except TypeError:
            raise TypeError("symbols has to be your alphabet, for example "
                            "run_test(..., symbols=COLORS)") from None
    if len(labels) < 2:
        raise ValueError("An alphabet needs at least two symbols.")
    if len(set(map(str, labels))) != len(labels):
        raise ValueError("Every symbol in the alphabet needs its own name.")
    return labels


def note_sent(run, position, symbol):
    """Notes that ``symbol`` now shows at ``position`` of the run.

    Called by run_test only, never by your own code: what counts as sent
    is decided by the test, not by the program being tested. The note goes
    into the measurement log of the sending device and to the server.
    """
    device = _roles["sender"]
    device._log.write("note_sent", run=run.id, position=position, symbol=symbol,
                      name=str(run.labels[symbol]))
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
                      symbol=symbol,
                      name=None if symbol is None else str(run.labels[symbol]))
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


def _check(run, answer):
    """The number of the symbol receive_symbol() named, or None if it named
    none of the alphabet (that counts as a mistake)."""
    for index, label in enumerate(run.labels):
        if type(answer) is type(label) and answer == label:
            return index
    if all(isinstance(label, int) for label in run.labels):
        expected = f"a whole number from 0 to {run.symbols - 1}"
    else:
        expected = "one of the names in your alphabet: " + ", ".join(
            repr(label) for label in run.labels)
    print(_paint(f"  receive_symbol() returned {answer!r}, but it has to return "
                 f"{expected}. Counted as a mistake.", "yellow"))
    return None


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
             runs=20, team=None, gain=None, integration_time=None):
    """The test for Challenge 1: how many symbols get through without a mistake?

    ``symbols`` is your alphabet, usually ``COLORS``: its names are the
    symbols. ``send_symbol(name)`` shows the symbol with that name.
    ``receive_symbol()`` measures and returns the name of the symbol it
    recognised. ``calibrate(readings)`` is called once before the test,
    after the whole alphabet has been shown once: ``readings`` is a
    dictionary from each name to a list of Readings taken while that symbol
    was showing, so ``readings["white"]``. What you make of them is up to you.

    With ``symbols=len(COLORS)``, as in the first version of the template,
    the symbols are the numbers 0 to ``len(COLORS) - 1`` instead of names.

    ``team`` is needed only if your devices are not registered with a team
    yet: then both laptops find each other by this name, and the run does
    not count.

    ``gain`` (1, 4, 16 or 60) and ``integration_time`` (2.4, 24, 101, 154 or
    700 milliseconds) set the sensor of the receiving device before the
    calibration. Without them it measures with 16x and 154 ms, as after
    every start. A laptop that only sends ignores them.

    Returns a dictionary with the number of hits, the two sequences by name
    and the sensor settings of the receiver.
    """
    if symbols is None:
        raise TypeError("run_test needs your alphabet, for example "
                        "run_test(..., symbols=COLORS)")
    # Check the settings first, so that a typo stops BOTH laptops before
    # anything happens, not only the receiving one halfway through
    if gain is not None and gain not in _GAINS:
        raise ValueError(f"There is no gain of {gain}x. "
                         f"Allowed: {sorted(_GAINS)}")
    if integration_time is not None and integration_time not in _INTEGRATION_TIMES:
        raise ValueError(f"There is no integration time of {integration_time} ms. "
                         f"Allowed: {sorted(_INTEGRATION_TIMES)}")
    labels = _labels(symbols)
    roles = _choose_roles()
    role = ("both" if roles["sender"] and roles["receiver"]
            else "sender" if roles["sender"] else "receiver")
    some_device = roles["sender"] or roles["receiver"]

    settings = {}
    if roles["receiver"] is not None:
        sensor = roles["receiver"].sensor
        if gain is not None:
            sensor.set_gain(gain)
        if integration_time is not None:
            sensor.set_integration_time(integration_time)
        settings = {"gain": sensor.gain, "integration_time": sensor.integration_time}

    run = _start(role, labels, runs, team, some_device, settings,
                 _alphabet(symbols, labels))
    if settings:
        print(f"Sensor of the receiver: gain {settings['gain']}x, "
              f"integration time {settings['integration_time']} ms.")
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
    return {**_report(run), **settings}


def _color(value):
    """A colour from the alphabet as [r, g, b] with 0 to 255, or None if it
    is written in a way this function does not know. Only for showing the
    alphabet in the cockpit and on the projector, never for the test."""
    try:
        if isinstance(value, str):
            text = value.strip().lstrip("#")
            if len(text) == 6:
                return [int(text[i:i + 2], 16) for i in (0, 2, 4)]
            return None
        parts = list(value)
        if len(parts) != 3:
            return None
        rgb = [int(round(float(x))) for x in parts]
        return rgb if all(0 <= x <= 255 for x in rgb) else None
    except (TypeError, ValueError):
        return None


def _alphabet(symbols, labels):
    """Names and colours of the alphabet for the server (since 0.8.0), so the
    projector view during an acceptance test can show the real colours."""
    colors = symbols if isinstance(symbols, dict) else {}
    return [{"name": str(label)[:40], "color": _color(colors.get(label))}
            for label in labels]


def _start(role, labels, runs, team, device, settings=None, alphabet=None):
    """Asks the server for a new run, or falls back to a practice run."""
    symbols = len(labels)
    if _server() is not None:
        try:
            payload = {
                "role": role, "led_uid": device.led.uid,
                "sensor_uid": device.sensor.uid,
                "master_uid": device.master_uid, "team": team,
                "symbols": symbols, "runs": runs,
                # the same id on every retry, so the server hands out the
                # same run instead of a second one (since 0.8.1)
                "join_id": secrets.token_urlsafe(12), **(settings or {})}
            if alphabet:
                payload["alphabet"] = alphabet
            state = _api("POST", "/api/runs/join", payload)
            run = _Run(role, labels, runs, practice=False, run_id=state["run_id"])
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
    run = _Run(role, labels, runs, practice=True, run_id="practice")
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
        send_symbol(run.labels[symbol])
        time.sleep(_settle_time())
        note_sent(run, position, symbol)
        if phase == "calibrate":
            readings[run.labels[symbol]] = _measure_calibration()
            note_received(run, position, None)
            print(_swatch(_shown_colour()) or ".", end="", flush=True)
        else:
            got = _check(run, receive_symbol())
            run.received.append(got)
            note_received(run, position, got)
            mark = _paint("ok", "green") if got == symbol else _paint("MISTAKE", "red", "bold")
            print(f"  {position - run.symbols + 1:>3}/{run.runs}  sent "
                  f"{_show(run, symbol, run.width)} {_swatch(_shown_colour())}"
                  f"recognised {_show(run, got, run.width)}  {mark}")
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
        send_symbol(run.labels[symbol])
        time.sleep(0.1)
        note_sent(run, position, symbol)
        if state["phase"] == "test":
            print(f"  {position - run.symbols + 1:>3}/{run.runs}  sent "
                  f"{_show(run, symbol, run.width)} {_swatch(_shown_colour())}".rstrip())
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
            readings[run.labels[state["symbol"]]] = _measure_calibration()
            note_received(run, position, None)
            print(".", end="", flush=True)
            continue
        if position == run.symbols:
            print()
            if calibrate is not None:
                calibrate(readings)
            _heading("Testing", f"{run.runs} symbols", end="\n")
        got = _check(run, receive_symbol())
        run.received.append(got)
        result = note_received(run, position, got)
        print(f"  {position - run.symbols + 1:>3}/{run.runs}  recognised "
              f"{_show(run, got, run.width)}")
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
            print(f"  {position:>3}: {_show(run, sent, run.width)} -> "
                  f"{_paint(_show(run, got), 'red')}")
        # The most common mix-up; answers outside the alphabet are no mix-up
        pairs = {}
        for _, sent, got in mistakes:
            if got is not None:
                pairs[(sent, got)] = pairs.get((sent, got), 0) + 1
        common = max(pairs.items(), key=lambda item: item[1], default=(None, 0))
        if common[1] > 1:
            (sent, got), count = common
            print(_paint(f"{_show(run, sent)} was taken for {_show(run, got)} {count} "
                         f"times: these two are probably too close together.", "yellow"))
    else:
        print(_paint("No mistakes.", "green"))
    if run.practice:
        print(_paint("This was a practice run; it does not count.", "yellow"))

    def name(index):
        return None if index is None else run.labels[index]

    return {"hits": hits, "runs": run.runs, "symbols": run.symbols,
            "sequence": [name(i) for i in sequence],
            "received": [name(i) for i in received],
            "practice": run.practice}
