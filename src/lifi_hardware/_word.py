"""Challenge 2: ``run_test`` with a ``codebook``.

In Challenge 2 a team sends whole sentences. The file of Challenge 1 grows
by a CODEBOOK (which colours stand for which character) and two functions,
``send_text(text)`` and ``receive_text(n_chars)``; the last line becomes

    run_test(send_text, receive_text, calibrate, symbols=COLORS,
             codebook=CODEBOOK, gain=GAIN, integration_time=INTEGRATION_TIME)

Giving a ``codebook`` is what tells run_test that this is Challenge 2.

A run has four parts: a calibration and three sentences. Each sentence is
three real words in capital letters with a space between them, drawn fresh
by the course server for every run, such as "BUSY FROGS DANCE". The sender
gets the sentence, the receiver only the number of characters.

The server gives each part a common start and nothing else. From the start
on, the code of the team keeps time: run_test calls ``send_text`` on the
sending device and ``receive_text`` on the receiving one at the same moment
and measures how long ``send_text`` takes. When it returns, the LED goes
off. With both devices on one laptop, both functions run side by side on
this laptop. With two laptops, each laptop asks the server for its clock a
few times and turns the start into its own clock; the two starts then agree
to within a few hundredths of a second, not exactly.

For the calibration, run_test shows every colour of COLORS once, a fixed
time each, with the values written in COLORS, and the receiver takes a few
readings of each. Then it calls ``calibrate(readings)`` as in Challenge 1.

A run counts when all three sentences arrive without a single mistake. Its
speed is the number of characters divided by the time ``send_text`` took,
in characters per second. Next to it stands the measured symbol rate, all
colours sent divided by the same time, and from it bits per second: the
symbol rate times log2 of the number of colours.

For a quick test in between, ``text="HELLO WORLD"`` sends your own text
instead of the sentences of the server (a list sends several, one after the
other). That is always a practice run with both devices on one laptop: it
does not ask the server and does not count.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import secrets
import threading
import time
import urllib.error

from . import _challenge as c

CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ "   # every character a sentence can hold
_HEARTBEAT = 2.0      # seconds between two signs of life during a long part
_LEAD = 1.5           # seconds between "ready" and the start in a practice run

# The words of a practice run; the course server has the same lists
_WORDS = (
    ("RED", "BLUE", "GREEN", "OLD", "YOUNG", "BIG", "SMALL", "TINY", "HAPPY",
     "SHY", "LOUD", "QUIET", "BRAVE", "LAZY", "WILD", "CALM", "FAST", "SLOW",
     "WARM", "COLD", "BUSY", "SILLY", "WISE", "PROUD", "NEAT", "BOLD", "KIND",
     "FUNNY", "SWEET", "GRAY"),
    ("CATS", "DOGS", "FOXES", "BIRDS", "BEARS", "FROGS", "BEES", "OWLS",
     "GOATS", "COWS", "MICE", "DUCKS", "FISH", "KIDS", "BOTS", "CHEFS",
     "KINGS", "CROWS", "ANTS", "BATS", "PIGS", "HENS", "SEALS", "CRABS",
     "LIONS", "MOLES", "YAKS", "MOTHS", "TOADS", "WORMS"),
    ("RUN", "JUMP", "SING", "DANCE", "SLEEP", "SWIM", "FLY", "LAUGH", "READ",
     "COOK", "PLAY", "HIDE", "WAIT", "SMILE", "BARK", "DREAM", "CLAP", "SHOUT",
     "WAVE", "DIVE", "ROAR", "HUM", "NAP", "SPIN", "HOP", "CHAT", "SKIP",
     "YAWN", "BLINK", "WALK"),
)


# --- Checking the codebook before anything happens ------------------------------

def _check_codebook(codebook, labels):
    """Stops with a clear message if the codebook cannot work, on both
    laptops, before the run starts. Returns the colours of each character."""
    if not isinstance(codebook, dict):
        raise TypeError("codebook has to be your CODEBOOK: a dictionary from "
                        "each character to a list of colour names")
    missing = [ch for ch in CHARSET if ch not in codebook]
    if missing:
        shown = ", ".join("the space" if ch == " " else ch for ch in missing)
        raise ValueError(f"Your CODEBOOK has no entry for {shown}. Every letter "
                         f"from A to Z and the space need one.")
    known = set(labels)
    seen = {}
    for ch in CHARSET:
        colours = codebook[ch]
        if isinstance(colours, str) or not isinstance(colours, (list, tuple)) \
                or not colours:
            raise ValueError(f"CODEBOOK[{ch!r}] has to be a list of colour names "
                             f"from COLORS, for example [\"off\", \"white\"].")
        for name in colours:
            if name not in known:
                raise ValueError(f"CODEBOOK[{ch!r}] uses {name!r}, which is not a "
                                 f"colour in COLORS.")
        key = tuple(colours)
        if key in seen:
            print(c._paint(f"Note: {seen[key]!r} and {ch!r} have the same colours in "
                           f"CODEBOOK, so the receiver cannot tell them apart.",
                           "yellow"))
        seen.setdefault(key, ch)


def _codebook_id(codebook):
    """A fingerprint of the codebook, so the server can tell whether both
    laptops run the same file."""
    text = json.dumps({str(k): [str(x) for x in v] for k, v in codebook.items()},
                      sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _rgb_values(symbols, labels):
    """The LED values of every colour, for the calibration."""
    if not isinstance(symbols, dict):
        raise TypeError("In Challenge 2, symbols has to be your COLORS, so that "
                        "run_test can show every colour for the calibration.")
    values = {}
    for label in labels:
        rgb = c._color(symbols[label])
        if rgb is None:
            raise ValueError(f"COLORS[{label!r}] has to be three numbers from 0 to "
                             f"255, for example (255, 0, 0).")
        values[label] = rgb
    return values


# --- Time -----------------------------------------------------------------------

def _clock_offset():
    """How far the clock of the server is ahead of this laptop, in seconds.

    Asks a few times and keeps the answer that came back fastest: the
    shorter the way, the less it can be off."""
    best = None
    for _ in range(5):
        before = time.time()
        try:
            now = c._api_once("GET", "/api/time", None)["now"]
        except Exception:
            continue
        after = time.time()
        if best is None or after - before < best[0]:
            best = (after - before, now - (before + after) / 2)
    if best is None:
        return c._api("GET", "/api/time")["now"] - time.time()
    return best[1]


def _sleep_until(moment):
    left = moment - time.time()
    if left > 0:
        time.sleep(left)


def _slot(integration_time):
    """How long each colour shows during the calibration (as on the server)."""
    return round(7 * integration_time / 1000 + 0.5, 3)


class _Heartbeat:
    """Tells the server now and then that this laptop is still there, while
    send_text or receive_text take their time. Without it, a long sentence
    would look like a laptop that has gone silent."""

    def __init__(self, run):
        self.run = run
        self.stopped = None
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if not self.run.practice:
            self._thread = threading.Thread(target=self._beat, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        return False

    def _beat(self):
        while not self._stop.wait(_HEARTBEAT):
            try:
                c._api_once("GET", f"/api/runs/{self.run.id}?role={self.run.role}", None)
            except urllib.error.HTTPError as error:
                if error.code in (404, 410):
                    try:
                        self.stopped = json.loads(error.read().decode("utf-8")).get("detail")
                    except (ValueError, AttributeError):
                        self.stopped = "the run has stopped"
                    print(c._paint(f"\n  {self.stopped}", "red"))
                    return
            except Exception:
                pass


# --- The run --------------------------------------------------------------------

def run_word_test(send_text, receive_text, calibrate, symbols, codebook,
                  sentences=3, team=None, gain=None, integration_time=None,
                  text=None):
    """Challenge 2, called by run_test when it gets a ``codebook``."""
    labels = c._labels(symbols)
    _check_codebook(codebook, labels)
    rgb = _rgb_values(symbols, labels)
    texts = _own_texts(text, codebook)
    if texts:
        sentences = len(texts)
    if not isinstance(sentences, int) or not 1 <= sentences <= 10:
        raise ValueError("sentences has to be a whole number from 1 to 10.")
    roles = c._choose_roles()
    role = ("both" if roles["sender"] and roles["receiver"]
            else "sender" if roles["sender"] else "receiver")
    if texts and role != "both":
        raise RuntimeError(
            "A run with your own text needs both devices on this laptop: it is a "
            "practice run without the course server. With two laptops, leave "
            "text= out.")
    some_device = roles["sender"] or roles["receiver"]

    settings = {}
    if roles["receiver"] is not None:
        sensor = roles["receiver"].sensor
        if gain is not None:
            sensor.set_gain(gain)
        if integration_time is not None:
            sensor.set_integration_time(integration_time)
        settings = {"gain": sensor.gain, "integration_time": sensor.integration_time}

    run = _start(role, labels, sentences, team, some_device, settings,
                 c._alphabet(symbols, labels), _codebook_id(codebook), texts)
    run.codebook = codebook
    run.rgb = rgb
    if settings:
        print(f"Sensor of the receiver: gain {settings['gain']}x, "
              f"integration time {settings['integration_time']} ms.")
    try:
        _run(run, send_text, receive_text, calibrate)
    finally:
        if roles["sender"] is not None:
            roles["sender"].led.off()
    return {**_report(run), **settings}


def _own_texts(text, codebook):
    """The texts of a practice run with your own text, checked against the
    codebook; None without text=."""
    if text is None:
        return None
    texts = [text] if isinstance(text, str) else text
    if not isinstance(texts, (list, tuple)) or not texts             or not all(isinstance(t, str) and t for t in texts):
        raise TypeError('text has to be a text such as "HELLO WORLD", or a list '
                        'of texts.')
    if len(texts) > 10:
        raise ValueError("text can hold at most 10 texts.")
    for t in texts:
        unknown = sorted({ch for ch in t if ch not in codebook})
        if unknown:
            shown = ", ".join(repr(ch) for ch in unknown)
            hint = (" Write it in capital letters." if any(ch.islower()
                    for ch in unknown) else "")
            raise ValueError(f"Your CODEBOOK has no entry for {shown} in "
                             f"{t!r}.{hint}")
    return list(texts)


def _start(role, labels, sentences, team, device, settings, alphabet, codebook_id,
           texts=None):
    """Asks the server for a new run of Challenge 2, or falls back to a
    practice run with both devices on this laptop. With your own texts it
    does not ask the server at all."""
    if texts:
        print(c._paint("Practice run with your own text: it does not count.",
                       "yellow"))
        run = c._Run(role, labels, sentences, practice=True, run_id="practice")
        run.sequence = texts
        run.total = sentences + 1
        return run
    if c._server() is not None:
        try:
            payload = {
                "challenge": 2, "role": role, "led_uid": device.led.uid,
                "sensor_uid": device.sensor.uid, "master_uid": device.master_uid,
                "team": team, "symbols": len(labels), "runs": sentences,
                "codebook_id": codebook_id, "alphabet": alphabet,
                "join_id": secrets.token_urlsafe(12), **(settings or {})}
            state = c._api("POST", "/api/runs/join", payload)
            run = c._Run(role, labels, sentences, practice=False,
                         run_id=state["run_id"])
            run.total = state["total"]
            note = (c._paint("counts", "green") if state["counted"] else
                    c._paint("does NOT count (devices not registered)", "yellow"))
            print(f"Run for {c._paint(state['team'], 'bold')}: {sentences} sentences "
                  f"with {len(labels)} colours, this run {note}.")
            return run
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
    if role != "both":
        raise RuntimeError(
            "The course server cannot be reached. With two laptops, the server "
            "gives both the common start, so this does not work offline. Check "
            "the WLAN, or plug both devices into one laptop for a practice run.")
    print(c._paint("The course server cannot be reached: PRACTICE RUN, it does not "
                   "count.", "yellow"))
    run = c._Run(role, labels, sentences, practice=True, run_id="practice")
    rng = random.SystemRandom()
    run.sequence = [" ".join(rng.choice(group) for group in _WORDS)
                    for _ in range(sentences)]
    run.total = sentences + 1
    return run


def _next_part(run, position, offset):
    """Says ready for this part and waits for its common start. Returns the
    phase, the start in the clock of this laptop and what the part needs."""
    if run.practice:
        receiver = c._roles["receiver"]
        if position == 0:
            return {"phase": "calibrate", "start": time.time() + _LEAD,
                    "slot": _slot(receiver.sensor.integration_time)}
        text = run.sequence[position - 1]
        return {"phase": "sentence", "start": time.time() + _LEAD,
                "sentence": position - 1, "text": text, "chars": len(text)}
    state = c._api("POST", f"/api/runs/{run.id}/ready",
                   {"role": run.role, "position": position})
    if state.get("start") is None:
        state = c._wait(run, lambda s: s["state"] != "running"
                        or s["position"] != position or s.get("start") is not None,
                        "the other laptop")
    if state["state"] != "running" or state["position"] != position:
        raise RuntimeError("The run went out of step with the server. Start "
                           "run_test again.")
    return {**state, "start": state["start"] - offset}


def _side_by_side(sending, receiving):
    """Runs the part of the sender in a second thread and the part of the
    receiver here, both at the same time; with only one of them, just that."""
    if sending is None:
        return None, receiving()
    if receiving is None:
        return sending(), None
    result, failure = {}, {}

    def send():
        try:
            result["value"] = sending()
        except BaseException as error:          # shown here, raised below
            failure["error"] = error
            print(c._paint(f"\n  send_text stopped with an error: {error!r}. "
                           f"If the receiver keeps waiting, press Ctrl+C.", "red"))

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    received = receiving()
    thread.join()
    if "error" in failure:
        raise failure["error"]
    return result.get("value"), received


def _run(run, send_text, receive_text, calibrate):
    sends = run.role in ("sender", "both")
    receives = run.role in ("receiver", "both")
    if not run.practice and run.role != "both":
        other = "receiving" if run.role == "sender" else "sending"
        c._wait(run, lambda s: s["state"] != "waiting", f"the {other} laptop")
    offset = 0.0 if run.practice else _clock_offset()
    run.received, run.durations, run.colours = [], [], []
    led = c._roles["sender"].led if sends else None

    for position in range(run.total):
        part = _next_part(run, position, offset)
        start = part["start"]

        if part["phase"] == "calibrate":
            slot = part["slot"]
            c._heading("Calibrating", "showing every colour once ")

            def show():
                for i, label in enumerate(run.labels):
                    _sleep_until(start + i * slot)
                    led.set_color(*run.rgb[label])
                    print(c._swatch(run.rgb[label]) or ".", end="", flush=True)
                _sleep_until(start + len(run.labels) * slot)
                led.off()

            def measure():
                it = c._roles["receiver"].sensor.integration_time / 1000
                readings = {}
                for i, label in enumerate(run.labels):
                    _sleep_until(start + i * slot + 2 * it + 0.25)
                    readings[label] = c._measure_calibration()
                    if not sends:
                        print(".", end="", flush=True)
                return readings

            with _Heartbeat(run):
                _, readings = _side_by_side(show if sends else None,
                                            measure if receives else None)
            print()
            if receives and calibrate is not None:
                calibrate(readings)
            _note(run, position, sent={"duration": 0, "colors": 0} if sends else None,
                  received={"text": None} if receives else None)
            continue

        index, chars = part["sentence"], part["chars"]
        text = part.get("text")
        c._heading(f"Sentence {index + 1} of {run.runs}",
                   f"{chars} characters", end="\n")
        if sends:
            print(f"  sending    {c._paint(text, 'bold')}")

        def send():
            _sleep_until(start)
            began = time.time()
            send_text(text)
            duration = time.time() - began
            led.off()
            return duration

        def receive():
            _sleep_until(start)
            return receive_text(chars)

        with _Heartbeat(run):
            duration, got = _side_by_side(send if sends else None,
                                          receive if receives else None)
        if receives:
            got = _check_text(got, chars)
            print(f"  received   {c._paint('-' if got is None else got, 'bold')}")
        sent_note = None
        if sends:
            colours = sum(len(run.codebook[ch]) for ch in text)
            sent_note = {"duration": round(duration, 4), "colors": colours}
            print(c._paint(f"  {colours} colours in {duration:.2f} s", "dim"))
        _note(run, position, sent=sent_note,
              received={"text": got} if receives else None)


def _check_text(got, chars):
    """The text receive_text returned, or None if it is no text at all."""
    if not isinstance(got, str):
        print(c._paint(f"  receive_text returned {got!r}, but it has to return the "
                       f"text it received. Counted as a mistake.", "yellow"))
        return None
    if len(got) != chars:
        print(c._paint(f"  receive_text returned {len(got)} characters, the sentence "
                       f"has {chars}.", "yellow"))
    return got


def _note(run, position, sent=None, received=None):
    """Tells the server (or, in a practice run, this module) that the part
    is done on this laptop, and waits until the other side is done too."""
    for kind, note in (("sent", sent), ("received", received)):
        if note is None:
            continue
        role = "sender" if kind == "sent" else "receiver"
        device = c._roles[role]
        device._log.write(f"word_{kind}", run=run.id, position=position, **note)
        if run.practice:
            if position > 0 and kind == "sent":
                run.durations.append(note["duration"])
                run.colours.append(note["colors"])
            if position > 0 and kind == "received":
                run.received.append(note["text"])
            continue
        c._api("POST", f"/api/runs/{run.id}/{kind}",
               {"role": run.role, "position": position, **note})
    if not run.practice:
        state = c._wait(run, lambda s: s["state"] != "running"
                        or s["position"] > position, "the other laptop")
        if state["state"] == "done":
            run.state = state


# --- The result -----------------------------------------------------------------

def _marked(sent, got):
    """The received text with every wrong character in red."""
    if got is None:
        return c._paint("-", "red")
    out = []
    for i, ch in enumerate(got):
        right = i < len(sent) and sent[i] == ch
        shown = ch if ch != " " or right else "_"
        out.append(shown if right else c._paint(shown, "red", "bold"))
    return "".join(out)


def _report(run):
    if run.practice:
        sequence, received = run.sequence, run.received
        durations, colours = run.durations, run.colours
    else:
        state = getattr(run, "state", None) or c._api(
            "GET", f"/api/runs/{run.id}?role={run.role}")
        sequence, received = state["sequence"], state["received"]
        durations, colours = state["durations"], state["colors"]
    print()
    hits = 0
    for i, (sent, got) in enumerate(zip(sequence, received)):
        right = sent == got
        hits += right
        mark = c._paint("ok", "green") if right else c._paint("MISTAKE", "red", "bold")
        print(f"  {i + 1}  sent      {sent}")
        print(f"     received  {_marked(sent, got)}   {mark}   "
              f"{durations[i]:.2f} s")
    total = sum(durations)
    chars = sum(len(s) for s in sequence)
    symbols = len(run.labels)
    rates = {}
    if total > 0:
        symbol_rate = sum(colours) / total
        rates = {"chars_per_s": round(chars / total, 2),
                 "symbols_per_s": round(symbol_rate, 2),
                 "bits_per_s": round(symbol_rate * math.log2(symbols), 2)}
    perfect = hits == len(sequence)
    kind = "Practice run" if run.practice else "Result"
    line = f"{kind}: {hits} of {len(sequence)} sentences without a mistake"
    if rates:
        line += (f", {rates['chars_per_s']:.2f} characters per second "
                 f"({rates['symbols_per_s']:.2f} colours per second, "
                 f"{rates['bits_per_s']:.2f} bit per second with {symbols} colours)")
    print(c._paint(line + ".", "bold", "green" if perfect else "red"))
    if not perfect:
        _hint(sequence, received)
    if run.practice:
        print(c._paint("This was a practice run; it does not count.", "yellow"))
    elif perfect and len(sequence) < 3:
        print(c._paint("Only runs with 3 sentences count for the challenge.", "yellow"))
    return {"hits": hits, "sentences": len(sequence), "sequence": sequence,
            "received": received, "durations": durations, **rates,
            "practice": run.practice}


def _hint(sequence, received):
    """What the mistakes suggest, as compare.py did before."""
    short = any(got is not None and len(got) < len(sent)
                for sent, got in zip(sequence, received))
    long = any(got is not None and len(got) > len(sent)
               for sent, got in zip(sequence, received))
    swapped = any(got is not None and len(got) == len(sent) and got != sent
                  for sent, got in zip(sequence, received))
    if short or long:
        print(c._paint("Characters went missing or appeared twice: sender and "
                       "receiver were probably out of step. That points to timing.",
                       "yellow"))
    if swapped:
        print(c._paint("Single characters came out wrong: colours were mixed up. "
                       "Try a slower SYMBOL_RATE, or look at the calibration.",
                       "yellow"))
