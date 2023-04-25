"Test shellous output redirect behavior."

import io
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from shellous import sh

# For Path, bytearray, and io.Base subclasses, writing to a sink should first
# truncate the sink. Appending to a sink, should leave the original contents
# in place.


async def test_redirect_path():
    "Test redirecting to an existing Path."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"
        tmp.write_text("12345")

        out = await (sh("echo", "abc") | tmp)
        assert out == ""
        assert tmp.read_text().rstrip() == "abc"


async def test_redirect_path_append():
    "Test redirecting to an existing Path with append."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"
        tmp.write_text("12345")

        out = await (sh("echo", "abc") >> tmp)
        assert out == ""
        assert tmp.read_text().rstrip() == "12345abc"


async def test_redirect_bytearray():
    "Test redirecting to an existing bytearray."
    buf = bytearray(b"12345")
    out = await (sh("echo", "abc") | buf)
    assert out == ""
    assert buf.rstrip() == b"abc"


async def test_redirect_bytearray_append():
    "Test redirecting to an existing bytearray with append."
    buf = bytearray(b"12345")
    out = await (sh("echo", "abc") >> buf)
    assert out == ""
    assert buf.rstrip() == b"12345abc"


async def test_redirect_textfile():
    "Test redirecting to an existing file."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"

        with open(tmp, "w") as fp:
            fp.write("12345")
            out = await (sh("echo", "abc") | fp)

        assert out == ""
        assert tmp.read_text().rstrip() == "abc"


async def test_redirect_textfile_append():
    "Test redirecting to an existing file with append."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"

        with open(tmp, "w") as fp:
            fp.write("12345")
            out = await (sh("echo", "abc") >> fp)

        assert out == ""
        assert tmp.read_text().rstrip() == "12345abc"


async def test_redirect_textfile_readwrite():
    "Test redirecting to an existing file open in read/write mode."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"
        tmp.write_text("12345")

        with open(tmp, "r+") as fp:
            out = await (sh("echo", "abc") | fp)

        assert out == ""
        assert tmp.read_text().rstrip() == "abc"


async def test_redirect_textfile_append_readwrite():
    "Test redirecting to an existing file open in read/write mode with append."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"
        tmp.write_text("12345")

        with open(tmp, "r+") as fp:
            out = await (sh("echo", "abc") >> fp)

        assert out == ""
        assert tmp.read_text().rstrip() == "12345abc"


async def test_redirect_binaryfile():
    "Test redirecting to an existing file."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"

        with open(tmp, "wb") as fp:
            fp.write(b"12345")
            out = await (sh("echo", "abc") | fp)

        assert out == ""
        assert tmp.read_text().rstrip() == "abc"


async def test_redirect_binaryfile_append():
    "Test redirecting to an existing file with append."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"

        with open(tmp, "wb") as fp:
            fp.write(b"12345")
            out = await (sh("echo", "abc") >> fp)

        assert out == ""
        assert tmp.read_text().rstrip() == "12345abc"


async def test_redirect_binaryfile_readonly():
    "Test redirecting to an existing file open in readonly mode."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"
        tmp.write_text("12345")

        with open(tmp, "rb") as fp:
            # Writing to a readonly file should cause an OSError.
            with pytest.raises(OSError):
                _ = await (sh("echo", "abc") | fp)

        assert tmp.read_text().rstrip() == "12345"


async def test_redirect_stringio():
    "Test redirecting to an existing StringIO buffer."
    buf = StringIO("12345")
    out = await (sh("echo", "abc") | buf)
    assert out == ""
    assert buf.getvalue().rstrip() == "abc"


async def test_redirect_stringio_append():
    "Test redirecting to an existing StringIO buffer."
    buf = StringIO("12345")
    out = await (sh("echo", "abc") >> buf)
    assert out == ""
    assert buf.getvalue().rstrip() == "12345abc"


async def test_redirect_bytesio():
    "Test redirecting to an existing StringIO buffer."
    buf = BytesIO(b"12345")
    out = await (sh("echo", "abc") | buf)
    assert out == ""
    assert buf.getvalue().rstrip() == b"abc"


async def test_redirect_bytesio_append():
    "Test redirecting to an existing StringIO buffer."
    buf = BytesIO(b"12345")
    out = await (sh("echo", "abc") >> buf)
    assert out == ""
    assert buf.getvalue().rstrip() == b"12345abc"


# For int, Redirect, Logger and StreamWriter, there is no difference between
# writing and appending. The result is always an append.


async def test_redirect_devnull_append():
    "Test appending to /dev/null."
    out = await (sh("echo", "abc") >> sh.DEVNULL)
    assert out == ""


async def test_redirect_int():
    "Test redirecting output to a file descriptor."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"
        tmp.write_bytes(b"1234567890")

        with open(tmp, "r+b", buffering=0) as fp:
            fp.seek(3, io.SEEK_SET)
            out = await (sh("echo", "abc") | fp.fileno())

        # There is no truncate/seek behavior on a raw file descriptor.
        assert out == ""
        assert tmp.read_bytes() in (b"123abc\n890", b"123abc\r\n890")


async def test_redirect_int_append():
    "Test appending to a file descriptor (same behavior as `test_redirect_int`)."
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "testfile"
        tmp.write_bytes(b"1234567890")

        with open(tmp, "r+b", buffering=0) as fp:
            fp.seek(3, io.SEEK_SET)
            out = await (sh("echo", "abc") >> fp.fileno())

        # There is no seek behavior on a raw file descriptor.
        assert out == ""
        assert tmp.read_bytes() in (b"123abc\n890", b"123abc\r\n890")


# TODO: Logger and StreamWriter
