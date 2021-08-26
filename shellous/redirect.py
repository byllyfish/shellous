"Implements the Redirect enum and various redirection utilities."

import asyncio
import enum
import functools
import io

from shellous.log import LOGGER
from shellous.util import decode, log_method


class Redirect(enum.IntEnum):
    "Redirection constants."

    STDOUT = asyncio.subprocess.STDOUT  # -2
    DEVNULL = asyncio.subprocess.DEVNULL  # -3
    CAPTURE = -10
    INHERIT = -11

    def is_custom(self):
        "Return true if this redirect option is not built into asyncio."
        return self in {Redirect.CAPTURE, Redirect.INHERIT}


@log_method(True)
async def write_stream(input_bytes, stream):
    try:
        if input_bytes:
            stream.write(input_bytes)
            await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        # Catch these errors and quietly stop.
        # See "_feed_stdin" in /3.9/Lib/asyncio/subprocess.py
        pass
    finally:
        stream.close()


@log_method(True)
async def copy_stringio(source, dest, encoding):
    # Collect partial reads into a BytesIO.
    buf = io.BytesIO()
    try:
        while True:
            data = await source.read(1024)
            if not data:
                break
            buf.write(data)
    finally:
        # Only convert to string once all output is collected.
        # (What if utf-8 codepoint is split between reads?)
        dest.write(decode(buf.getvalue(), encoding))


@log_method(True)
async def copy_bytesio(source, dest):
    # Collect partial reads into a BytesIO.
    while True:
        data = await source.read(1024)
        if not data:
            break
        dest.write(data)


@log_method(True)
async def copy_bytearray(source, dest):
    # Collect partial reads into a bytearray.
    while True:
        data = await source.read(1024)
        if not data:
            break
        dest.extend(data)
