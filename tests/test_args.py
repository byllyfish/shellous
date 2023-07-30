"Unit tests for Command's arguments with common Python types."

import re
from collections.abc import Iterable, Iterator, Mapping
from io import BytesIO, StringIO
from ipaddress import ip_address, ip_network
from pathlib import Path

import pytest

from shellous import Command, sh


def _not_supported(cmd: Command):
    name, arg = cmd.args
    assert isinstance(arg, str)
    return (
        name == "echo"
        and re.match(r"<[<>_.a-z ]+ at 0x[0-9a-f]+>", arg, re.IGNORECASE) is not None
    )


def test_str_and_bytes():
    "Strings and bytes are handled correctly."
    cmd = sh("echo", "a", b"b")
    assert cmd.args == ("echo", "a", b"b")


def test_bytearray():
    "Bytearray is converted to immutable bytes."
    cmd = sh("echo", bytearray(b"abc"))
    assert cmd.args == ("echo", b"abc")
    assert isinstance(cmd.args[1], bytes)


def test_memoryview():
    "memoryview is handled correctly."
    buf = bytearray(b"abcdef")
    cmd = sh("echo", memoryview(buf))
    assert _not_supported(cmd)


def test_numbers():
    "Numbers are handled correctly."
    cmd = sh("echo", 1, 2.3, -1, 1 + 2j, True, False)
    assert cmd.args == ("echo", "1", "2.3", "-1", "(1+2j)", "True", "False")


def test_none():
    "None is not supported."
    with pytest.raises(NotImplementedError, match="syntax is reserved"):
        sh("echo", None)


def test_ellipsis():
    "Ellipsis is not supported."
    with pytest.raises(NotImplementedError, match="syntax is reserved"):
        sh("echo", ...)


def test_command_pipeline():
    "Commands and pipelines (for process substitution) are supported."
    cmd1 = sh("echo")
    pipe1 = sh("echo") | sh("cat")
    cmd = sh("echo", cmd1, pipe1)
    assert cmd.args == ("echo", cmd1, pipe1)


def test_stringio():
    "StringIO is handled correctly."
    cmd = sh("echo", StringIO("abc"))
    assert _not_supported(cmd)


def test_bytesio():
    "BytesIO is handled correctly."
    cmd = sh("echo", BytesIO(b"abc"))
    assert _not_supported(cmd)


def test_path():
    "Path is handled correctly."
    cmd = sh("echo", Path("/tmp/abc"))
    assert cmd.args == ("echo", Path("/tmp/abc"))  # FIXME: ?


def test_ipaddress():
    "IPAddress is handled correctly."
    cmd = sh("echo", ip_address(0x0A000001), ip_address("2000::1"))
    assert cmd.args == ("echo", "10.0.0.1", "2000::1")


def test_ipnetwork():
    "IPNetwork is handled correctly."
    cmd = sh("echo", ip_network("10.0.1.0/24"), ip_network("2000::0/64"))
    assert cmd.args == ("echo", "10.0.1.0/24", "2000::/64")


def test_tuple():
    "Tuples are flattened recursively."
    cmd = sh("echo", ((1, 2), 3))
    assert cmd.args == ("echo", "1", "2", "3")


def test_reverse_tuple():
    cmd = sh("echo", reversed((1, 2, 3)))
    assert _not_supported(cmd)


def test_list():
    "Lists are flattened recursively."
    cmd = sh("echo", [[1, 2], 3])
    assert cmd.args == ("echo", "1", "2", "3")


def test_generator():
    "Generators are flattened recursively."

    def agen(num):
        for i in range(1, num + 1):
            yield i

    cmd = sh("echo", agen(3))
    assert _not_supported(cmd)


def test_range():
    cmd = sh("echo", range(3))
    assert cmd.args == ("echo", "range(0, 3)")  # FIXME: iterable


def test_enumerate():
    cmd = sh("echo", enumerate([1, 2, 3]))
    assert _not_supported(cmd)


def test_dict():
    with pytest.raises(NotImplementedError, match="syntax is reserved"):
        sh("echo", dict(a=1))


def test_set():
    with pytest.raises(NotImplementedError, match="syntax is reserved"):
        sh("echo", {1, 2, 3})


def test_frozenset():
    cmd = sh("echo", frozenset([1, 2, 3]))
    assert cmd.args == ("echo", "frozenset({1, 2, 3})")  # FIXME: iterable


def test_sorted_set():
    cmd = sh("echo", sorted({1, 2, 3}))
    assert cmd.args == ("echo", "1", "2", "3")


class _NoRepr:
    pass


def test_class():
    cmd = sh("echo", _NoRepr())
    assert _not_supported(cmd)


def test_function():
    cmd = sh("echo", test_class)
    assert _not_supported(cmd)


def test_mapping():
    cmd = sh("echo", _TestMap(a=1, b=2))
    assert _not_supported(cmd)


def test_iterable():
    cmd = sh("echo", _TestIterable(1, 2, 3))
    assert _not_supported(cmd)


def test_iterator():
    cmd = sh("echo", _TestIterator(1, 2, 3))
    assert _not_supported(cmd)


class _TestMap(Mapping):
    def __init__(self, **values: int):
        self._values = values

    def __len__(self):
        return len(self._values)

    def __getitem__(self, item):
        return self._values[item]

    def __iter__(self):
        return iter(self._values)


class _TestIterable(Iterable):
    def __init__(self, *values: int):
        self._values = values

    def __iter__(self):
        return iter(self._values)


class _TestIterator(Iterator):
    def __init__(self, *values: int):
        self._values = list(values)

    def __iter__(self):
        return self

    def __next__(self):
        if not self._values:
            raise StopIteration
        return self._values.pop()
