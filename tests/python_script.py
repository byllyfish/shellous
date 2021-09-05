# Python script used in `test_shellous.py`.

import os
import sys
import time

SHELLOUS_CMD = os.environ.get("SHELLOUS_CMD")
SHELLOUS_EXIT_CODE = int(os.environ.get("SHELLOUS_EXIT_CODE") or 0)
SHELLOUS_EXIT_SLEEP = int(os.environ.get("SHELLOUS_EXIT_SLEEP") or 0)


def _write(data):
    "Write data to stdout as bytes."
    if data:
        sys.stdout.buffer.write(data)


if SHELLOUS_CMD == "echo":
    data = b" ".join(arg.encode("utf-8") for arg in sys.argv[1:])
    _write(data)

elif SHELLOUS_CMD == "cat":
    if len(sys.argv) > 1:
        with open(sys.argv[1], "rb") as afile:
            while data := afile.read(4096):
                _write(data)
    else:
        while data := sys.stdin.buffer.read(4096):
            _write(data)

elif SHELLOUS_CMD == "sleep":
    time.sleep(float(sys.argv[1]))

elif SHELLOUS_CMD == "env":
    data = b"".join(
        f"{key}={value}\n".encode("utf-8") for key, value in os.environ.items()
    )
    _write(data)

elif SHELLOUS_CMD == "tr":
    data = sys.stdin.buffer.read()
    _write(data.upper())

elif SHELLOUS_CMD == "bulk":
    _write(b"1234" * (1024 * 1024 + 1))

else:
    raise NotImplementedError

if SHELLOUS_EXIT_SLEEP:
    sys.stdout.buffer.flush()
    time.sleep(float(SHELLOUS_EXIT_SLEEP))

sys.exit(SHELLOUS_EXIT_CODE)
