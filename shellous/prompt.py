"Implements the Prompt utility class."

import asyncio
import enum
import re
from typing import Optional, Union

from shellous import pty_util
from shellous.harvest import harvest_results
from shellous.log import LOG_PROMPT, LOGGER
from shellous.runner import Runner
from shellous.util import decode_bytes, encode_bytes

_EOL_REGEX = re.compile(rb"\r\n|\r")


class _Cue(enum.Enum):
    "Alternate prompt types."
    DEFAULT = enum.auto()
    EOF = enum.auto()


class Prompt:
    """Utility class to help with an interactive prompt session.

    This is an **experimental** API.

    - A `prompt` is a text string that marks the end of some output, and
      indicates that some new input is desired. In an interactive python
      session, the prompt is typically ">>> ".

    Example:
    ```
    cmd = sh("sh").stdin(sh.CAPTURE).stdout(sh.CAPTURE).stderr(sh.STDOUT)

    async with cmd.env(PS1="??? ") as run:
        cli = Prompt(run, prompt="??? ")
        greeting = await cli.receive()

        result = await cli.send("echo hello")
        assert result == "hello\n"

        cli.close()
    ```
    """

    EOF = _Cue.EOF

    _runner: Runner
    _encoding: str
    _default_end: str
    _default_prompt: bytes
    _default_timeout: Optional[float]
    _normalize_newlines: bool
    _at_eof: bool = False

    def __init__(
        self,
        runner: Runner,
        *,
        default_end: str = "\n",
        default_prompt: str = "",
        default_timeout: Optional[float] = None,
        normalize_newlines: bool = False,
    ):
        assert runner.stdin is not None
        assert runner.stdout is not None

        self._runner = runner
        self._encoding = runner.command.options.encoding
        self._default_end = default_end
        self._default_prompt = encode_bytes(default_prompt, self._encoding)
        self._default_timeout = default_timeout
        self._normalize_newlines = normalize_newlines

    @property
    def run(self) -> Runner:
        "The runner object for the process."
        return self._runner

    @property
    def at_eof(self) -> bool:
        return self._at_eof

    @property
    def echo(self) -> bool:
        """True if TTY is in echo mode.

        If the runner is not using a PTY, return False.
        """
        if self._runner.pty_fd is None:
            return False
        return pty_util.get_term_echo(self._runner.pty_fd)

    @echo.setter
    def echo(self, value: bool) -> None:
        """Set echo mode for the PTY.

        Raise an error if the runner is not using a PTY.
        """
        if self._runner.pty_fd is None:
            raise RuntimeError("Cannot set echo mode. Not running in a PTY.")
        pty_util.set_term_echo(self._runner.pty_fd, value)

    async def send(
        self,
        input_text: str,
        *,
        end: Optional[str] = None,
        prompt: Union[str, _Cue, None] = _Cue.DEFAULT,
        timeout: Optional[float] = None,
        noecho: bool = False,
    ) -> str:
        """Write some input text to stdin, then await the response from stdout.

        Raises:
            EOFError: if receive() has already reached EOF.
        """
        if self._at_eof:
            raise EOFError("Prompt at EOF")

        if end is None:
            end = self._default_end

        if noecho:
            await self._wait_noecho()

        data = encode_bytes(input_text + end, self._encoding)

        stdin = self._runner.stdin
        assert stdin is not None
        stdin.write(data)

        if LOG_PROMPT:
            if noecho:
                LOGGER.debug("Prompt[pid=%s] send: [[HIDDEN]]", self._runner.pid)
            else:
                LOGGER.debug("Prompt[pid=%s] send: %r", self._runner.pid, data)

        # Drain our write to stdin and wait for prompt from stdout.
        cancelled, (result, _) = await harvest_results(
            self._read_to_prompt(prompt),
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
        prompt: Union[str, _Cue] = _Cue.DEFAULT,
        timeout: Optional[float] = None,
    ) -> str:
        """Read from stdout up to the next prompt, or EOF if prompt not found."""
        if self._at_eof:
            raise EOFError("Prompt at EOF")

        cancelled, (result,) = await harvest_results(
            self._read_to_prompt(prompt),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()

        assert isinstance(result, str)
        return result

    async def expect(
        self,
        pattern: re.Pattern[str],
        *,
        timeout: Optional[float] = None,
    ) -> tuple[str, re.Match[str]]:
        """Read from stdout until the regular expression matches.

        Use the `expect` method when you need to read up to a prompt that
        varies.

        ```
        _, m = await cli.expect(re.compile(r'Login: |Password: |ftp> '))
        match m[0]:
            case "Login: ":
                await cli.send(user, prompt=None)
            case "Password: ":
                await cli.send(password, prompt=None)
            case "ftp> ":
                result = await cli.send(command)
        ```
        """
        if self._at_eof:
            raise EOFError("Prompt at EOF")
        raise NotImplementedError()  # TODO

    def close(self) -> None:
        "Close stdin to end the prompt session."
        stdin = self._runner.stdin
        assert stdin is not None

        if self._runner.pty_eof:
            # Write EOF twice; once to end the current line, and the second
            # time to signal the end.
            stdin.write(self._runner.pty_eof * 2)
            if LOG_PROMPT:
                LOGGER.debug("Prompt[pid=%s] send: [[EOF]]", self._runner.pid)

        else:
            stdin.close()

    async def _read_to_prompt(self, prompt: Union[str, _Cue, None]) -> str:
        """Read all data up to the prompt and return it (after removing prompt).

        This method sets `at_eof` if we encounter EOF instead of the prompt.
        """
        if not prompt:
            return ""

        if prompt == _Cue.EOF:
            return await self._read_to_eof()

        if prompt == _Cue.DEFAULT:
            prompt_bytes = self._default_prompt
        else:
            prompt_bytes = encode_bytes(prompt, self._encoding)

        # If the prompt is "", do not read *anything*.
        if not prompt_bytes:
            return ""

        stdout = self._runner.stdout
        assert stdout is not None

        buf = await _read_until(stdout, prompt_bytes)
        if LOG_PROMPT:
            LOGGER.debug("Prompt[pid=%s] receive: %r", self._runner.pid, buf)

        # Replace CR-LF or CR with LF.
        if self._normalize_newlines:
            buf = _normalize_eol(buf)

        # TODO: Test case where prompt itself contains a CR-LF...

        # Clean up the output to remove the prompt, then return as string.
        if buf.endswith(prompt_bytes):
            buf = buf[0 : -len(prompt_bytes)]
        else:
            self._at_eof = True

        return decode_bytes(buf, self._encoding)

    async def _read_to_eof(self) -> str:
        "Read all data to the end."
        stdout = self._runner.stdout
        assert stdout is not None

        buf = await stdout.read()
        if LOG_PROMPT:
            LOGGER.debug("Prompt[pid=%s] receive: %r", self._runner.pid, buf)

        # Replace CR-LF or CR with LF.
        if self._normalize_newlines:
            buf = _normalize_eol(buf)

        self._at_eof = True

        return decode_bytes(buf, self._encoding)

    async def _wait_noecho(self):
        "Wait for terminal echo mode to be disabled."
        if LOG_PROMPT:
            LOGGER.debug(
                "Prompt[pid=%s] wait: noecho",
                self._runner.pid,
            )

        for _ in range(4 * 30):
            if not self.echo:
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("Timed out: Terminal echo mode remains enabled.")


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
    except asyncio.CancelledError:
        if LOG_PROMPT:
            LOGGER.debug(
                "Prompt._read_until cancelled with buffer contents: %r",
                stream._buffer,
            )
        raise

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


def _normalize_eol(buf: bytes) -> bytes:
    """Normalize end of lines."""
    return _EOL_REGEX.sub(b"\n", buf)
