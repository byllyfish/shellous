import os

import pytest
from shellous.util import close_fds, coerce_env, decode

pytestmark = pytest.mark.asyncio


def test_decode():
    "Test the util.decode function."
    assert decode(None, None) == b""
    assert decode(b"", None) == b""
    assert decode(b"abc", None) == b"abc"

    assert decode(b"", "utf-8") == ""
    assert decode(b"abc", "utf-8") == "abc"
    assert decode(None, "utf-8") == ""

    with pytest.raises(UnicodeDecodeError):
        decode(b"\x81", "utf-8")

    assert decode(b"\x81abc", "utf-8 replace") == "\ufffdabc"


def test_coerce_env():
    "Test the util.coerce_env function."
    result = coerce_env(dict(a="a", b=1))
    assert result == {"a": "a", "b": "1"}

    # Test on Windows using SystemRoot env, otherwise test with PATH.
    if "SystemRoot" in os.environ:
        result = coerce_env(dict(SystemRoot=...))
        assert result == {"SystemRoot": os.environ["SystemRoot"]}
    else:
        result = coerce_env(dict(PATH=...))
        assert result == {"PATH": os.environ["PATH"]}


def test_close_fds(tmp_path):
    "Test the close_fds() function."
    out = tmp_path / "test_close_fds"

    with open(out, "w") as fp:
        fd = fp.fileno()
        open_files = [fp, fd]

    close_fds(open_files)
    assert not open_files
