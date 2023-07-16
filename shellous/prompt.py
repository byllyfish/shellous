"Implements the Prompt utility class."

import asyncio
from typing import Optional

from shellous.harvest import harvest_results
from shellous.log import LOGGER
from shellous.runner import Runner


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

    runner: Runner
    prompt_bytes: bytes
    default_timeout: Optional[float]

    def __init__(
        self,
        runner: Runner,
        prompt: str,
        *,
        default_timeout: Optional[float] = None,
    ):
        assert runner.stdin is not None
        assert runner.stdout is not None

        self.runner = runner
        self.prompt_bytes = prompt.encode("utf-8")
        self.default_timeout = default_timeout

    async def send(
        self,
        input_text: str,
        *,
        timeout: Optional[float] = None,
    ) -> str:
        "Write some input text to stdin, then await the response."
        stdin = self.runner.stdin
        assert stdin is not None

        data = input_text.encode("utf-8") + b"\n"
        LOGGER.debug("Prompt[pid=%s] send: %r", self.runner.pid, data)
        stdin.write(data)

        # Drain our write to stdin and wait for prompt from stdout.
        cancelled, (result, _) = await harvest_results(
            self._read_to_prompt(),
            stdin.drain(),
            timeout=timeout or self.default_timeout,
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
            timeout=timeout or self.default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()

        assert isinstance(result, str)
        return result

    def close(self) -> None:
        "Close stdin to end the prompt session."
        assert self.runner.stdin is not None
        self.runner.stdin.close()

    async def _read_to_prompt(self) -> str:
        "Read all data up to the prompt and return it (after removing prompt)."
        stdout = self.runner.stdout
        assert stdout is not None

        buf = await _read_until(stdout, self.prompt_bytes)
        LOGGER.debug("Prompt[pid=%s] receive: %r", self.runner.pid, buf)

        # Clean up the output to remove the prompt, then return as string.
        buf = buf.replace(b"\r\n", b"\n")
        if buf.endswith(self.prompt_bytes):
            buf = buf[0 : -len(self.prompt_bytes)]

        return buf.decode("utf-8")


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
