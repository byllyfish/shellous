"Implements support for Results."

from dataclasses import dataclass
from typing import Any, Optional, Union

from shellous.util import decode


class ResultError(Exception):
    "Represents a non-zero exit status."

    @property
    def result(self):
        return self.args[0]

    @property
    def command(self):
        return self.args[1]


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
        return PipeResult(result.exit_code, result.cancelled)


def make_result(command, result):
    "Convert list of results into a single pipe result."

    if isinstance(result, list):
        last = result[-1]
        result = Result(
            last.output_bytes,
            last.exit_code,
            last.cancelled,
            last.encoding,
            tuple(PipeResult.from_result(r) for r in result[0:-1]),
        )

    allowed_exit_codes = command.options.allowed_exit_codes or {0}
    if result.exit_code not in allowed_exit_codes:
        raise ResultError(result, command)

    if command.options.return_result:
        return result
    return result.output
