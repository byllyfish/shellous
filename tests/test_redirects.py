"Test shellous output redirect behavior."

from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

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

# TODO
