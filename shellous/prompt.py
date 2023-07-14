"Implements the Prompt utility class."

import asyncio
from typing import Optional

from shellous.runner import Runner
from shellous.harvest import harvest_results


class Prompt:
    """Utility class to help with an interactive prompt session.

    This is an experimental API.

    Example:
    ```
    cmd = sh("sh").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.env(PS1="??? ") as run:
        prompt = Prompt(run, "??? ")

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
        input_text: str = "",
        *,
        timeout: float | None = None,
    ) -> str:
        "Write some input text to stdin, then await the response."
        stdin = self.runner.stdin
        stdout = self.runner.stdout
        assert stdin is not None
        assert stdout is not None

        if timeout is None:
            timeout = self.default_timeout

        if input_text:
            stdin.write(input_text.encode("utf-8") + b"\n")

        # Drain our write to stdin, and wait for prompt from stdout.
        cancelled, (buf, _) = await harvest_results(
            _read_until(stdout, self.prompt_bytes),
            stdin.drain(),
            timeout=timeout,
        )
        assert isinstance(buf, bytes)

        if cancelled:
            raise asyncio.CancelledError()

        # Clean up the output to remove the prompt, then return as string.
        buf = buf.replace(b"\r\n", b"\n")
        if buf.endswith(self.prompt_bytes):
            prompt_len = len(self.prompt_bytes)
            buf = buf[0:-prompt_len].rstrip(b"\n")

        return buf.decode("utf-8")

    def close(self):
        "Close stdin to end the prompt session."
        assert self.runner.stdin is not None
        self.runner.stdin.close()


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
