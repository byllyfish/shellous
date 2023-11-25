"Implements the Prompt utility class."

import asyncio
import codecs
import io
import re
from typing import Optional, Union

from shellous import pty_util
from shellous.harvest import harvest_results
from shellous.log import LOG_PROMPT, LOGGER
from shellous.result import Result
from shellous.runner import Runner
from shellous.util import encode_bytes

_DEFAULT_LINE_END = "\n"
_DEFAULT_CHUNK_SIZE = 16384
_LOG_LIMIT = 2000


class Prompt:
    """Utility class to help with an interactive prompt session.

    This is an **experimental** API.

    - A `prompt` is a text string that marks the end of some output, and
      indicates that some new input is desired. In an interactive python
      session, the prompt is typically ">>> ".

    You will usually create a `Prompt` class using the `prompt()` API.

    Example:
    ```
    cmd = sh("sh").env(PS1="??? ").set(pty=True)

    async with cmd.prompt("??? ", timeout=3.0) as cli:
        # Turn off terminal echo.
        cli.echo = False

        # Wait for initial prompt.
        greeting, _ = await cli.expect()

        # Send a command and wait for the response.
        await cli.send("echo hello")
        answer, _ = await cli.expect()
        assert answer == "hello\r\n"
    ```
    """

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
    _result: Optional[Result] = None

    def __init__(
        self,
        runner: Runner,
        *,
        default_end: Optional[str] = None,
        default_prompt: Union[str, re.Pattern[str], None] = None,
        default_timeout: Optional[float] = None,
        normalize_newlines: bool = False,
        chunk_size: Optional[int] = None,
    ):
        assert runner.stdin is not None
        assert runner.stdout is not None

        if default_end is None:
            default_end = _DEFAULT_LINE_END

        if chunk_size is None:
            chunk_size = _DEFAULT_CHUNK_SIZE

        if isinstance(default_prompt, str):
            default_prompt = re.compile(re.escape(default_prompt))

        self._runner = runner
        self._encoding = runner.command.options.encoding
        self._default_end = default_end
        self._default_prompt = default_prompt
        self._default_timeout = default_timeout
        self._normalize_newlines = normalize_newlines
        self._chunk_size = chunk_size
        self._decoder = _decoder(self._encoding, normalize_newlines)

    @property
    def at_eof(self) -> bool:
        "True if the prompt reader is at the end of file."
        return self._at_eof and not self._pending

    @property
    def pending(self) -> str:
        """Characters still unread in the `pending` buffer."""
        return self._pending

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

    @property
    def result(self) -> Optional[Result]:
        "The `Result` of the command, or None if it has not exited yet."
        return self._result

    async def send(
        self,
        input_text: Union[bytes, str],
        *,
        end: Optional[str] = None,
        no_echo: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """Write some input text to stdin."""
        if end is None:
            end = self._default_end

        if no_echo:
            await self._wait_no_echo()

        if isinstance(input_text, bytes):
            data = input_text + encode_bytes(end, self._encoding)
        else:
            data = encode_bytes(input_text + end, self._encoding)

        stdin = self._runner.stdin
        assert stdin is not None
        stdin.write(data)

        if LOG_PROMPT:
            if no_echo:
                LOGGER.debug("Prompt[pid=%s] send: [[HIDDEN]]", self._runner.pid)
            else:
                data_len = len(data)
                if data_len >= _LOG_LIMIT:
                    LOGGER.debug(
                        "Prompt[pid=%s] send: [%d B] %r...",
                        self._runner.pid,
                        data_len,
                        data[:_LOG_LIMIT],
                    )
                else:
                    LOGGER.debug(
                        "Prompt[pid=%s] send: [%d B] %r",
                        self._runner.pid,
                        data_len,
                        data,
                    )

        # Drain our write to stdin.
        cancelled, ex = await harvest_results(
            self._drain(stdin),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()
        elif isinstance(ex[0], Exception):
            raise ex[0]

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

        To match multiple patterns, use the regular expression alternation
        syntax:

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
        *,
        timeout: Optional[float] = None,
    ) -> str:
        """Read all characters until EOF.

        If we are already at EOF, return "".
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
        end: Optional[str] = None,
        no_echo: bool = False,
        prompt: Union[str, re.Pattern[str], None] = None,
        timeout: Optional[float] = None,
    ) -> str:
        "Send some input and receive the response up to the next prompt."
        if self._at_eof:
            raise EOFError("Prompt at EOF")

        await self.send(input_text, end=end, no_echo=no_echo, timeout=timeout)
        result, _ = await self.expect(prompt, timeout=timeout)
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
            if LOG_PROMPT:
                LOGGER.debug("Prompt[pid=%s] close", self._runner.pid)

    def _finish_(self) -> None:
        "Internal method called when process exits to fetch the `Result` and cache it."
        self._result = self._runner.result(check=False)

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

            _prev_len = len(self._pending)  # debug check

            try:
                # Read chunk and check for EOF.
                chunk = await stdout.read(self._chunk_size)
                if not chunk:
                    self._at_eof = True
            except asyncio.CancelledError:
                if LOG_PROMPT:
                    LOGGER.debug(
                        "Prompt[pid=%s] receive cancelled: pending=%r",
                        self._runner.pid,
                        self._pending,
                    )
                raise

            if LOG_PROMPT:
                chunk_len = len(chunk)
                if chunk_len >= _LOG_LIMIT:
                    LOGGER.debug(
                        "Prompt[pid=%s] receive: [%d B] %r...",
                        self._runner.pid,
                        chunk_len,
                        chunk[:_LOG_LIMIT],
                    )
                else:
                    LOGGER.debug(
                        "Prompt[pid=%s] receive: [%d B] %r",
                        self._runner.pid,
                        chunk_len,
                        chunk,
                    )

            # Decode eligible bytes into our buffer.
            data = self._decoder.decode(chunk, final=self._at_eof)
            if not data and not self._at_eof:
                continue
            self._pending += data

            result = self._search_pending(pattern)
            if result is not None:
                return result

            assert len(self._pending) > _prev_len  # debug check

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

    async def _drain(self, stream: asyncio.StreamWriter) -> None:
        "Drain stream while reading into buffer concurrently."
        read_task = asyncio.create_task(self._read_some())
        try:
            await stream.drain()

        finally:
            if not read_task.done():
                read_task.cancel()
                await read_task

    async def _read_some(self) -> None:
        "Read into buffer until cancelled or EOF. Used during _drain() only."
        stdout = self._runner.stdout
        assert stdout is not None
        assert self._chunk_size > 0

        while not self._at_eof:
            # Yield time to other tasks; read() doesn't yield as long as there
            # is data to read.
            await asyncio.sleep(0)

            # Read chunk and check for EOF.
            chunk = await stdout.read(self._chunk_size)
            if not chunk:
                self._at_eof = True

            if LOG_PROMPT:
                chunk_len = len(chunk)
                if chunk_len >= _LOG_LIMIT:
                    LOGGER.debug(
                        "Prompt[pid=%s] receive@drain: [%d B] %r...",
                        self._runner.pid,
                        chunk_len,
                        chunk[:_LOG_LIMIT],
                    )
                else:
                    LOGGER.debug(
                        "Prompt[pid=%s] receive@drain: [%d B] %r",
                        self._runner.pid,
                        chunk_len,
                        chunk,
                    )

            # Decode eligible bytes into our buffer.
            data = self._decoder.decode(chunk, final=self._at_eof)
            self._pending += data

    async def _wait_no_echo(self):
        "Wait for terminal echo mode to be disabled."
        if LOG_PROMPT:
            LOGGER.debug(
                "Prompt[pid=%s] wait: no_echo",
                self._runner.pid,
            )

        for _ in range(4 * 30):
            if not self.echo:
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("Timed out: Terminal echo mode remains enabled.")


def _decoder(encoding: str, normalize_newlines: bool) -> codecs.IncrementalDecoder:
    "Construct an incremental decoder for the specified encoding settings."
    enc = encoding.split(maxsplit=1)
    decoder_class = codecs.getincrementaldecoder(enc[0])
    decoder = decoder_class(*enc[1:])
    if normalize_newlines:
        decoder = io.IncrementalNewlineDecoder(decoder, translate=True)
    return decoder
