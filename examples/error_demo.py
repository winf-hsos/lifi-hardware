"""Test script for the crash events (since lifi_hardware 0.3.0).

Triggers four typical errors one after the other, each in its own
subprocess (a program can only crash once, after all). In the cockpit they
appear as red marks, on the server as error events:

    python error_demo.py            # all four cases one after the other
    python error_demo.py name       # trigger only one case

The cases:
    name    NameError         (typo in a variable name)
    type    TypeError         (number plus text)
    file    FileNotFoundError (the message contains a path; in the upload
                               it must have become <path>)
    value   ValueError        (library error: integration time not allowed)
"""

import subprocess
import sys
import time

CASES = ["name", "type", "file", "value"]

if len(sys.argv) == 1:
    for case in CASES:
        print(f"--- case: {case}")
        subprocess.run([sys.executable, __file__, case])
        time.sleep(0.5)
    print()
    print("Four errors triggered. Have a look:")
    print("  Cockpit: https://lifi.uber.space/cockpit?team=pilot-01")
    print("  (red marks on the timeline, tile 'last error')")
    sys.exit(0)

from lifi_hardware import LifiDevice

lifi = LifiDevice.connect(log_file=None)
case = sys.argv[1]

if case == "name":
    sensr.read()                          # noqa: F821, wrong on purpose
elif case == "type":
    result = 1 + "1"
elif case == "file":
    open(r"C:\does-not-exist\readings.txt")
elif case == "value":
    lifi.sensor.set_integration_time(50)  # there is no 50 ms
else:
    print(f"Unknown case: {case}")
