"Unit tests for the Result class."

import signal

import pytest

from shellous import Result


def _new_result(exit_code):
    return Result(
        exit_code=exit_code,
        output_bytes=b"output",
        error_bytes=b"error",
        cancelled=False,
        encoding="utf-8",
    )


def test_result_okay():
    "Test the Result class."
    result = _new_result(0)
    assert result
    assert result.exit_code == 0
    assert result.exit_signal is None
    assert result.encoding == "utf-8"
    assert result.output_bytes == b"output"
    assert result.error_bytes == b"error"
    assert result.output == "output"
    assert result.error == "error"


def test_result_error():
    "Test the Result class with exit_code 1."
    result = _new_result(1)
    assert not result
    assert result.exit_code == 1
    assert result.exit_signal is None


def test_result_signal():
    "Test the Result class with exit_code -1."
    result = _new_result(-signal.SIGINT)
    assert not result
    assert result.exit_code == -signal.SIGINT
    assert result.exit_signal == signal.SIGINT


def test_result_signal_unknown():
    "Test the Result class with exit_code -1000."
    result = _new_result(-1000)
    assert not result
    assert result.exit_code == -1000

    with pytest.raises(ValueError, match="not a valid Signals"):
        _ = result.exit_signal
