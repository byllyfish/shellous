"Implements the Prompt utility class."

import asyncio
import codecs
import enum
import io
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


def _get_decoder(encoding: str, normalize_newlines: bool) -> codecs.IncrementalDecoder:
    enc = encoding.split(maxsplit=1)
    decoder_class = codecs.getincrementaldecoder(enc[0])
    decoder = decoder_class(*enc[1:])
    if normalize_newlines:
        decoder = io.IncrementalNewlineDecoder(decoder, translate=True)
    return decoder


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
    _default_prompt: Optional[re.Pattern[str]]
    _default_timeout: Optional[float]
    _normalize_newlines: bool
    _chunk_size: int
    _decoder: codecs.IncrementalDecoder
    _pending: str = ""
    _at_eof: bool = False

    def __init__(
        self,
        runner: Runner,
        *,
        default_end: str = "\n",
        default_prompt: Union[str, re.Pattern[str], None] = None,
        default_timeout: Optional[float] = None,
        normalize_newlines: bool = False,
        chunk_size: int = 4096,
    ):
        assert runner.stdin is not None
        assert runner.stdout is not None

        if isinstance(default_prompt, str):
            default_prompt = re.compile(re.escape(default_prompt))

        self._runner = runner
        self._encoding = runner.command.options.encoding
        self._default_end = default_end
        self._default_prompt = default_prompt
        self._default_timeout = default_timeout
        self._normalize_newlines = normalize_newlines
        self._chunk_size = chunk_size
        self._decoder = _get_decoder(self._encoding, normalize_newlines)

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
        timeout: Optional[float] = None,
        no_echo: bool = False,
    ) -> None:
        """Write some input text to stdin, then await the response from stdout.

        Raises:
            EOFError: if receive() has already reached EOF.
        """
        if self._at_eof:
            raise EOFError("Prompt at EOF")

        if end is None:
            end = self._default_end

        if no_echo:
            await self._wait_no_echo()

        data = encode_bytes(input_text + end, self._encoding)

        stdin = self._runner.stdin
        assert stdin is not None
        stdin.write(data)

        if LOG_PROMPT:
            if no_echo:
                LOGGER.debug("Prompt[pid=%s] send: [[HIDDEN]]", self._runner.pid)
            else:
                LOGGER.debug("Prompt[pid=%s] send: %r", self._runner.pid, data)

        # Drain our write to stdin.
        cancelled, _ = await harvest_results(
            stdin.drain(),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()

    async def expect(
        self,
        prompt: Union[str, re.Pattern[str], None] = None,
        *,
        timeout: Optional[float] = None,
    ) -> tuple[str, Optional[re.Match[str]]]:
        """Read from stdout until the regular expression matches.


        A `prompt` is usually a regular expression object. If `prompt` is a
        string, the string must match exactly. If `prompt` is None or missing,
        we use the default prompt.

        ```
        _, m = await cli.expect(re.compile(r'Login: |Password: |ftp> '))
        if m:
            match m[0]:
                case "Login: ":
                    await cli.send(user)
                case "Password: ":
                    await cli.send(password)
                case "ftp> ":
                    await cli.send(command)
        ```
        """
        if isinstance(prompt, str):
            prompt = re.compile(re.escape(prompt))
        elif prompt is None:
            prompt = self._default_prompt
            if prompt is None:
                raise TypeError("prompt is required when default prompt is not set")

        if self._pending:
            result = self._search_pending(prompt)
            if result is not None:
                return result

        if self._at_eof:
            raise EOFError("Prompt at EOF")

        cancelled, (result,) = await harvest_results(
            self._read_to_pattern(prompt),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()

        return result

    async def read_all(
        self,
        timeout: Optional[float] = None,
    ) -> str:
        """Read all characters until EOF.

        When we are at EOF, return "".
        """
        if self._pending:
            result = self._search_pending(None)
            if result is not None:
                return result[0]

        if self._at_eof:
            return ""

        cancelled, (result,) = await harvest_results(
            self._read_to_pattern(None),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()

        return result[0]

    async def command(
        self,
        input_text: str,
        *,
        timeout: Optional[float] = None,
    ) -> str:
        "Send some input and receive the response up to the next prompt."
        # TODO: These can be done in separate tasks to avoid potential deadlock.
        await self.send(input_text, timeout=timeout)
        result, _ = await self.expect(timeout=timeout)
        return result

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

    async def _read_to_pattern(
        self,
        pattern: Optional[re.Pattern[str]],
    ) -> tuple[str, Optional[re.Match[str]]]:
        """Read text up to part that matches pattern.

        If `pattern` is None, read all data until EOF.

        Returns 2-tuple with (text, match). `match` is None if there is no
        match because we've reached EOF.
        """
        stdout = self._runner.stdout
        assert stdout is not None
        assert self._chunk_size > 0

        while True:
            assert not self._at_eof

            _initial_len = len(self._pending)

            # Read chunk and check for EOF.
            chunk = await stdout.read(self._chunk_size)
            if not chunk:
                self._at_eof = True

            if LOG_PROMPT:
                LOGGER.debug("Prompt[pid=%s] receive: %r", self._runner.pid, chunk)

            # Decode eligible bytes into our buffer.
            data = self._decoder.decode(chunk, final=self._at_eof)
            if not data and not self._at_eof:
                continue
            self._pending += data

            result = self._search_pending(pattern)
            if result is not None:
                return result

            assert len(self._pending) > _initial_len

    def _search_pending(
        self,
        pattern: Optional[re.Pattern[str]],
    ) -> Optional[tuple[str, Optional[re.Match[str]]]]:
        """Search pending buffer for pattern."""
        if pattern is not None:
            # Search our `_pending` buffer for the pattern. If we find a match,
            # we return it and prepare any unread data in the buffer for the
            # next "_read" call.
            found = pattern.search(self._pending)
            if found:
                if LOG_PROMPT:
                    LOGGER.debug("Prompt[pid=%s] found: %r", self._runner.pid, found)
                result = self._pending[0 : found.start(0)]
                self._pending = self._pending[found.end(0) :]
                return (result, found)

        if self._at_eof:
            # Pattern doesn't match anything and we've reached EOF.
            if LOG_PROMPT:
                LOGGER.debug(
                    "Prompt[pid=%s] at_eof: %d chars pending",
                    self._runner.pid,
                    len(self._pending),
                )
            result = self._pending
            self._pending = ""
            return (result, None)

        return None

    async def _wait_no_echo(self):
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
