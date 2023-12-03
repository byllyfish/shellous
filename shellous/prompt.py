"Implements the Prompt utility class."

import asyncio
import codecs
import io
import re
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from types import EllipsisType  # requires python 3.10

from shellous import pty_util
from shellous.harvest import harvest_results
from shellous.log import LOG_PROMPT, LOGGER
from shellous.result import Result
from shellous.runner import Runner
from shellous.util import encode_bytes

_DEFAULT_LINE_END = "\n"
_DEFAULT_CHUNK_SIZE = 16384

_LOG_LIMIT = 2000
_LOG_LIMIT_END = 30


class Prompt:
    """Utility class to help with an interactive prompt session.

    When you are controlling to a co-process you will usually `send` some
    text to it, and then `expect` a response. The `expect` operation can use
    a regular expression to match different types of responses.

    Create a new `Prompt` instance using the `prompt()` API.

    ```
    # In this example, we are using a default prompt of "??? ".
    # Setting PS1 tells the shell to use this as the shell prompt.
    cmd = sh("sh").env(PS1="??? ").set(pty=True)

    async with cmd.prompt("??? ", timeout=3.0) as cli:
        # Turn off terminal echo.
        cli.echo = False

        # Wait for greeting and initial prompt.
        greeting, _ = await cli.expect()

        # Send a command and wait for the response.
        await cli.send("echo hello")
        answer, _ = await cli.expect()
        assert answer == "hello\r\n"
    ```
    """

    _runner: Runner
    _encoding: str
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
        default_prompt: Union[str, list[str], re.Pattern[str], None] = None,
        default_timeout: Optional[float] = None,
        normalize_newlines: bool = False,
        chunk_size: Optional[int] = None,
    ):
        assert runner.stdin is not None
        assert runner.stdout is not None

        if chunk_size is None:
            chunk_size = _DEFAULT_CHUNK_SIZE

        if isinstance(default_prompt, (str, list)):
            default_prompt = _regex_compile_exact(default_prompt)

        self._runner = runner
        self._encoding = runner.command.options.encoding
        self._default_prompt = default_prompt
        self._default_timeout = default_timeout
        self._normalize_newlines = normalize_newlines
        self._chunk_size = chunk_size
        self._decoder = _make_decoder(self._encoding, normalize_newlines)

    @property
    def at_eof(self) -> bool:
        "True if the prompt reader is at the end of file."
        return self._at_eof and not self._pending

    @property
    def pending(self) -> str:
        """Characters that still remain in the `pending` buffer."""
        return self._pending

    @property
    def echo(self) -> bool:
        """True if PTY is in echo mode.

        If the runner is not using a PTY, always return False.

        When the process is using a PTY, you can enable/disable terminal echo
        mode by setting the `echo` property to True/False.
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
        "The `Result` of the command. Returns None if process has not exited yet."
        return self._result

    @property
    def runner(self) -> Runner:
        "The process runner."
        return self._runner

    async def send(
        self,
        text: Union[bytes, str],
        *,
        end: Optional[str] = _DEFAULT_LINE_END,
        no_echo: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """Write some `text` to co-process standard input and append a newline.

        The `text` parameter is the string that you want to write. Use a `bytes`
        object to send some raw bytes (e.g. to the terminal driver).

        The default line ending is "\n". Use the `end` parameter to change the
        line ending. To omit the line ending entirely, specify `end=None`.

        Set `no_echo` to True when you are writing a password. When you set
        `no_echo` to True, the `send` method will wait for terminal
        echo mode to be disabled before writing the text. If shellous logging
        is enabled, the sensitive information will **not** be logged.

        Use the `timeout` parameter to override the default timeout. Normally,
        data is delivered immediately and this method returns making a trip
        through the event loop. However, there are situations where the
        co-process input pipeline fills and we have to wait for it to
        drain.

        When this method needs to wait for the co-process input pipe to drain,
        this method will concurrently read from the output pipe into the pending
        buffer. This is necessary to avoid a deadlock situation where everything
        stops because neither process can make progress.
        """
        if end is None:
            end = ""

        if no_echo:
            await self._wait_no_echo()

        if isinstance(text, bytes):
            data = text + encode_bytes(end, self._encoding)
        else:
            data = encode_bytes(text + end, self._encoding)

        stdin = self._runner.stdin
        assert stdin is not None
        stdin.write(data)

        if LOG_PROMPT:
            self._log_send(data, no_echo)

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
        prompt: Union[str, list[str], re.Pattern[str], "EllipsisType", None] = None,
        *,
        timeout: Optional[float] = None,
    ) -> tuple[str, Optional[re.Match[str]]]:
        """Read from co-process standard output until `prompt` pattern matches.

        Returns a 2-tuple of (output, match) where `output` is the text *before*
        the prompt pattern and `match` is a `re.Match` object for the prompt
        text itself. If there is no match due to EOF, `match` will be None.

        After this method returns, there may still be characters read from stdout
        that remain in the `pending` buffer. These are the characters *after* the
        prompt pattern that didn't match. You can examine these using the `pending`
        property. Subsequent calls to expect will examine this buffer first
        before reading new data from the output pipe.

        If a timeout is reached while waiting for the prompt pattern, this
        method will raise an `asyncio.TimeoutError` exception. You can catch
        this exception and look in the `pending` buffer to see the data that
        failed to match. You can call send()/expect() again after a timeout
        to negotiate further with the co-process.

        Once EOF has been reached, calling this method again will continue to
        return ("", None).

        By default, this method will use the default `timeout` for the `Prompt`
        object if one is set. You can use the `timeout` parameter to specify
        a custom timeout in seconds.

        Prompt Patterns
        ~~~~~~~~~~~~~~~

        The `expect()` method supports matching fixed strings and regular
        expressions. The type of the `prompt` parameter determines the type of
        search.

        `None`:
            Use the default prompt pattern. If there is no default prompt,
            raise a TypeError.
        `str`:
            Match this string exactly.
        `list[str]`:
            Match one of these strings exactly.
        `re.Pattern[str]`:
            Match the given regular expression.
        `...`:
            Read all data until end of stream (EOF).

        When matching a regular expression, only a single Pattern object is
        supported. To match multiple regular expressions, combine them into a
        single regular expression using *alternation* syntax (|).

        The `expect()` method returns a 2-tuple (output, match). The `match`
        is the result of the regular expression search (re.Match). If you
        specify your prompt as a string or list of strings, it is still compiled
        into a regular expression that produce an `re.Match` object. You can
        examine the `match` object to determine the prompt string found.

        This method conducts a regular expression search on streaming data. The
        `expect()` method reads a new chunk of data into the `pending` buffer
        and then searches it. You must be careful in writing a regular
        expression so that the search is agnostic to how the incoming chunks of
        data arrive. Instead of using `greedy` RE modifiers such as `*`, `+`,
        and `{m,n}`, use the `minimal` versions: `*?`, `+?`, and `{m,n}?`.

        It is best to use the `expect()` method to search for a trailing
        delimiter or prompt, read the relevant data into a variable, and then
        examine that variable in depth with a separate parser or regular
        expression.

        Examples
        ~~~~~~~~

        Expect an exact string:

        ```
        _, m = await cli.expect("ftp> ")
        if m:
            await cli.send(command)
            response, _ = await cli.expect("ftp> ")
        ```

        Expect a choice of strings:

        ```
        _, m = await cli.expect(["Login: ", "Password: ", "ftp> "])
        if m:
            match m[0]:
                case "Login: ":
                    await cli.send(login)
                case "Password: ":
                    await cli.send(password)
                case "ftp> ":
                    await cli.send(command)
        ```

        Read until EOF:

        ```
        data, _ = await cli.expect(...)
        ```

        """
        if prompt is ...:
            return await self.read_all(), None

        if prompt is None:
            prompt = self._default_prompt
            if prompt is None:
                raise TypeError("prompt is required when default prompt is not set")
        elif isinstance(prompt, (str, list)):
            prompt = _regex_compile_exact(prompt)

        if self._pending:
            result = self._search_pending(prompt)
            if result is not None:
                return result

        if self._at_eof:
            return ("", None)

        cancelled, (result,) = await harvest_results(
            self._read_to_pattern(prompt),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()
        if isinstance(result, Exception):
            raise result

        return result

    async def read_all(
        self,
        *,
        timeout: Optional[float] = None,
    ) -> str:
        """Read from co-process stdout  characters until EOF.

        If we are already at EOF, return "".

        Deprecated: Use `expect(...)`.
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
        text: str,
        *,
        end: str = _DEFAULT_LINE_END,
        no_echo: bool = False,
        prompt: Union[str, re.Pattern[str], "EllipsisType", None] = None,
        timeout: Optional[float] = None,
    ) -> str:
        "Send some input and receive the response up to the next prompt."
        if self._at_eof:
            raise EOFError("Prompt has reached EOF already")

        await self.send(text, end=end, no_echo=no_echo, timeout=timeout)
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
                self._log_receive(chunk)

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
                self._log_receive(chunk, "@drain")

            # Decode eligible bytes into our buffer.
            data = self._decoder.decode(chunk, final=self._at_eof)
            self._pending += data

    async def _wait_no_echo(self):
        "Wait for terminal echo mode to be disabled."
        if LOG_PROMPT:
            LOGGER.debug("Prompt[pid=%s] wait: no_echo", self._runner.pid)

        for _ in range(4 * 30):
            if not self.echo:
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("Timed out: Terminal echo mode remains enabled.")

    def _log_send(self, data: bytes, no_echo: bool):
        "Log data as it is being sent."
        pid = self._runner.pid

        if no_echo:
            LOGGER.debug("Prompt[pid=%s] send: [[HIDDEN]]", pid)
        else:
            data_len = len(data)
            if data_len > _LOG_LIMIT:
                LOGGER.debug(
                    "Prompt[pid=%s] send: [%d B] %r...%r",
                    pid,
                    data_len,
                    data[: _LOG_LIMIT - _LOG_LIMIT_END],
                    data[-_LOG_LIMIT_END:],
                )
            else:
                LOGGER.debug(
                    "Prompt[pid=%s] send: [%d B] %r",
                    pid,
                    data_len,
                    data,
                )

    def _log_receive(self, data: bytes, tag: str = ""):
        "Log data as it is being received."
        pid = self._runner.pid
        data_len = len(data)

        if data_len > _LOG_LIMIT:
            LOGGER.debug(
                "Prompt[pid=%s] receive%s: [%d B] %r...%r",
                pid,
                tag,
                data_len,
                data[: _LOG_LIMIT - _LOG_LIMIT_END],
                data[-_LOG_LIMIT_END:],
            )
        else:
            LOGGER.debug(
                "Prompt[pid=%s] receive%s: [%d B] %r",
                pid,
                tag,
                data_len,
                data,
            )


def _make_decoder(encoding: str, normalize_newlines: bool) -> codecs.IncrementalDecoder:
    "Construct an incremental decoder for the specified encoding settings."
    enc = encoding.split(maxsplit=1)
    decoder_class = codecs.getincrementaldecoder(enc[0])
    decoder = decoder_class(*enc[1:])
    if normalize_newlines:
        decoder = io.IncrementalNewlineDecoder(decoder, translate=True)
    return decoder


def _regex_compile_exact(pattern: Union[str, list[str]]) -> re.Pattern[str]:
    "Compile a regex that matches an exact string or set of strings."
    if isinstance(pattern, list):
        return re.compile("|".join(re.escape(s) for s in pattern))
    else:
        return re.compile(re.escape(pattern))
