"Python script used in `test_shellous.py`."

import os
import sys
import time

SHELLOUS_CMD = os.environ.get("SHELLOUS_CMD")
SHELLOUS_EXIT_CODE = int(os.environ.get("SHELLOUS_EXIT_CODE") or 0)
SHELLOUS_EXIT_SLEEP = float(os.environ.get("SHELLOUS_EXIT_SLEEP") or 0)


def _write(data):
    "Write data to stdout as bytes."
    try:
        if data:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    except BrokenPipeError:
        # https://docs.python.org/3/library/signal.html#note-on-sigpipe
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(1)  # Python exits with error code 1 on EPIPE


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

elif SHELLOUS_CMD == "count":
    arg = int(sys.argv[1])
    for i in range(arg):
        _write(f"{i+1}\n".encode("utf-8"))

else:
    raise NotImplementedError

if SHELLOUS_EXIT_SLEEP:
    sys.stdout.buffer.flush()
    time.sleep(float(SHELLOUS_EXIT_SLEEP))

sys.exit(SHELLOUS_EXIT_CODE)
