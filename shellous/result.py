"Implements support for Results."

from dataclasses import dataclass
from typing import Any, Optional, Union

from shellous.util import decode


class ResultError(Exception):
    "Represents a non-zero exit status."

    @property
    def result(self):
        "Return the `Result` object."
        return self.args[0]


@dataclass(frozen=True)
class Result:
    "Concrete class for the result of a Command."

    output_bytes: Optional[bytes]
    exit_code: int
    cancelled: bool
    encoding: Optional[str]
    extra: Any = None

    @property
    def output(self) -> Union[str, bytes, None]:
        "Return output string, based on encoding."
        return decode(self.output_bytes, self.encoding)


@dataclass
class PipeResult:
    "Concrete class for the result of command that is part of a Pipe."

    exit_code: int
    cancelled: bool

    @staticmethod
    def from_result(result):
        "Construct a `PipeResult` from a `Result`."
        if isinstance(result, ResultError):
            result = result.result
        return PipeResult(result.exit_code, result.cancelled)


def make_result(command, result):
    """Convert list of results into a single pipe result.

    `result` can be a list of Result, ResultError or another Exception.
    """

    if isinstance(result, list):
        # Check result list for other exceptions.
        other_ex = [
            ex
            for ex in result
            if isinstance(ex, Exception) and not isinstance(ex, ResultError)
        ]
        if other_ex:
            # Re-raise other exception here.
            raise other_ex[0]

        # Everything in result is now either a Result or ResultError.
        key_result = _find_key_result(result)
        last = _get_result(result[-1])

        result = Result(
            last.output_bytes,
            key_result.exit_code,
            key_result.cancelled,
            last.encoding,
            tuple(PipeResult.from_result(r) for r in result),
        )

    assert isinstance(result, Result)

    allowed_exit_codes = command.options.allowed_exit_codes or {0}
    if result.exit_code not in allowed_exit_codes:
        raise ResultError(result)

    if command.options.return_result:
        return result
    return result.output


def _find_key_result(result_list):
    "Scan a result list and return the 'key' result."
    acc = None
    for item in result_list:
        item = _get_result(item)
        acc = _compare_result(acc, item)
        # Return the first one with a non-zero exit code that isn't cancelled.
        if (not acc.cancelled, acc.exit_code != 0) == (True, True):
            return acc

    return acc


def _compare_result(acc, item):
    if acc is None:
        return item
    acc_key = (not acc.cancelled, acc.exit_code != 0)
    item_key = (not item.cancelled, acc.exit_code != 0)
    if item_key >= acc_key:
        return item
    return acc


def _get_result(item):
    if isinstance(item, ResultError):
        return item.result
    return item
