"Implements the Prompt utility class."

import asyncio
import re
from typing import Optional

from shellous.harvest import harvest_results
from shellous.log import LOGGER
from shellous.runner import Runner
from shellous.util import decode_bytes, encode_bytes

_EOL_REGEX = re.compile(rb"\r\n|\r")


class Prompt:
    """Utility class to help with an interactive prompt session.

    This is an experimental API.

    Example:
    ```
    cmd = sh("sh").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.env(PS1="??? ") as run:
        prompt = Prompt(run, "??? ")
        greeting = await prompt.receive()

        result = await prompt.send("echo hello")
        assert result == "hello\n"

        prompt.close()
    ```
    """

    _runner: Runner
    _encoding: str
    _prompt_bytes: bytes
    _default_timeout: Optional[float]

    def __init__(
        self,
        runner: Runner,
        prompt: str,
        *,
        default_timeout: Optional[float] = None,
        normalize_newlines: bool = False,
    ):
        assert runner.stdin is not None
        assert runner.stdout is not None

        self._runner = runner
        self._encoding = runner.command.options.encoding
        self._prompt_bytes = encode_bytes(prompt, self._encoding)
        self._default_timeout = default_timeout
        self._normalize_newlines = normalize_newlines

    async def send(
        self,
        input_text: str,
        *,
        timeout: Optional[float] = None,
    ) -> str:
        "Write some input text to stdin, then await the response from stdout."
        stdin = self._runner.stdin
        assert stdin is not None

        data = encode_bytes(input_text, self._encoding) + b"\n"
        LOGGER.debug("Prompt[pid=%s] send: %r", self._runner.pid, data)
        stdin.write(data)

        # Drain our write to stdin and wait for prompt from stdout.
        cancelled, (result, _) = await harvest_results(
            self._read_to_prompt(),
            stdin.drain(),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()

        assert isinstance(result, str)
        return result

    async def receive(
        self,
        *,
        timeout: Optional[float] = None,
    ) -> str:
        "Read from stdout up to the next prompt."
        cancelled, (result,) = await harvest_results(
            self._read_to_prompt(),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()

        assert isinstance(result, str)
        return result

    def close(self) -> None:
        "Close stdin to end the prompt session."
        assert self._runner.stdin is not None
        self._runner.stdin.close()

    async def _read_to_prompt(self) -> str:
        "Read all data up to the prompt and return it (after removing prompt)."
        stdout = self._runner.stdout
        assert stdout is not None

        buf = await _read_until(stdout, self._prompt_bytes)
        LOGGER.debug("Prompt[pid=%s] receive: %r", self._runner.pid, buf)

        # Replace CR-LF or CR with LF.
        if self._normalize_newlines:
            buf = _EOL_REGEX.sub(b"\n", buf)

        # Clean up the output to remove the prompt, then return as string.
        if buf.endswith(self._prompt_bytes):
            buf = buf[0 : -len(self._prompt_bytes)]

        return decode_bytes(buf, self._encoding)


async def _read_until(stream: asyncio.StreamReader, separator: bytes) -> bytes:
    "Read all data until the separator."
    try:
        # Most reads can complete without buffering.
        return await stream.readuntil(separator)
    except asyncio.IncompleteReadError as ex:
        return ex.partial
    except asyncio.LimitOverrunError as ex:
        # Okay, we have to buffer.
        buf = bytearray(await stream.read(ex.consumed))

    while True:
        try:
            buf.extend(await stream.readuntil(separator))
        except asyncio.IncompleteReadError as ex:
            buf.extend(ex.partial)
        except asyncio.LimitOverrunError as ex:
            buf.extend(await stream.read(ex.consumed))
            continue
        break

    return bytes(buf)
