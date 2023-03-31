"Implements support for Results."

import asyncio
import sys
from dataclasses import dataclass
from typing import Any, Optional, Union

import shellous
from shellous.util import decode

# Limit number of bytes of stderr stored in Result object.
RESULT_STDERR_LIMIT = 1024


class ResultError(Exception):
    "Represents a non-zero exit status."

    @property
    def result(self):
        "Result of the command."
        return self.args[0]


_KW_ONLY = {"kw_only": True} if sys.version_info >= (3, 10) else {}


@dataclass(frozen=True, **_KW_ONLY)
class Result:
    "Concrete class for the result of a Command."

    exit_code: int
    "Command's exit code."

    output_bytes: Optional[bytes]
    "Output of command as bytes. May be None if there is no output."

    error_bytes: bytes
    "Limited standard error from command if not redirected."

    cancelled: bool
    "Command was cancelled."

    encoding: str
    "Output encoding."

    extra: Any = None
    "Used for pipeline results (see `PipeResult`)."

    @property
    def output(self) -> str:
        "Output of command as a string."
        return decode(self.output_bytes, self.encoding)

    @property
    def error(self) -> str:
        "Error from command as a string (if it is not redirected)."
        return decode(self.error_bytes, self.encoding)

    def __bool__(self) -> bool:
        "Return true if exit_code is 0."
        return self.exit_code == 0


@dataclass
class PipeResult:
    "Concrete class for the result of command that is part of a Pipe."

    exit_code: int
    cancelled: bool

    @staticmethod
    def from_result(result: Union[BaseException, Result]):
        "Construct a `PipeResult` from a `Result`."
        if isinstance(result, ResultError):
            result = result.result
        assert isinstance(result, Result)
        return PipeResult(result.exit_code, result.cancelled)


def convert_result_list(
    result_list: list[Union[BaseException, Result]],
    cancelled: bool,
):
    "Convert list of results into a single pipe result."
    assert result_list is not None

    # Check result list for other exceptions.
    other_ex = [
        ex
        for ex in result_list
        if isinstance(ex, Exception) and not isinstance(ex, ResultError)
    ]
    if other_ex:
        # Re-raise other exception here.
        raise other_ex[0]

    # Everything in result is now either a Result or ResultError.
    key_result = _find_key_result(result_list)
    last = _get_result(result_list[-1])
    assert key_result is not None  # (pyright)

    return Result(
        exit_code=key_result.exit_code,
        output_bytes=last.output_bytes,
        error_bytes=last.error_bytes,
        cancelled=cancelled,
        encoding=last.encoding,
        extra=tuple(PipeResult.from_result(r) for r in result_list),
    )


def check_result(
    result: Result,
    options: "shellous.Options",
    cancelled: bool,
    timed_out: bool = False,
) -> Result:
    """Check result and raise exception if necessary."""
    if cancelled and not options.catch_cancelled_error:
        raise asyncio.CancelledError()

    exit_codes = options.exit_codes or {0}
    if (cancelled and not timed_out) or result.exit_code not in exit_codes:
        raise ResultError(result)

    return result


def _find_key_result(result_list: list[Union[Result, BaseException]]) -> Result:
    "Scan a result list and return the 'key' result."
    acc = None

    for item in result_list:
        item = _get_result(item)
        acc = _compare_result(acc, item)
        # Return the first one with a non-zero exit code that isn't cancelled.
        if (not acc.cancelled, acc.exit_code != 0) == (True, True):
            return acc

    if acc is None:
        raise RuntimeError(f"empty result: {result_list!r}")

    return acc


def _compare_result(acc: Optional[Result], item: Result) -> Result:
    if acc is None:
        return item
    acc_key = (not acc.cancelled, acc.exit_code != 0)
    item_key = (not item.cancelled, acc.exit_code != 0)
    if item_key >= acc_key:
        return item
    return acc


def _get_result(item: Union[Result, BaseException]) -> Result:
    if isinstance(item, ResultError):
        return item.result
    assert isinstance(item, Result)
    return item
