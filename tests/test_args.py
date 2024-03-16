"Unit tests for Command's arguments with common Python types."

import re
import shlex
from collections.abc import Iterable, Iterator, Mapping
from io import BytesIO, StringIO
from ipaddress import ip_address, ip_network
from pathlib import Path

import pytest

from shellous import Command, sh


def _str_not_implemented(cmd: Command):
    "Match command arguments against a type that doesn't implement str()."
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
    assert cmd.args == ("echo", b"abcdef")
    assert isinstance(cmd.args[1], bytes)


def test_numbers():
    "Numbers are handled correctly."
    cmd = sh("echo", 1, 2.3, -1, 1 + 2j, True, False)
    assert cmd.args == ("echo", "1", "2.3", "-1", "(1+2j)", "True", "False")


def test_none():
    "None is not supported."
    with pytest.raises(TypeError, match="not supported"):
        sh("echo", None)

    with pytest.raises(TypeError, match="not supported"):
        sh(None)


def test_ellipsis():
    "Ellipsis is not supported."
    with pytest.raises(TypeError, match="not supported"):
        sh("echo", ...)

    with pytest.raises(TypeError, match="not supported"):
        sh(...)


def test_command_pipeline():
    "Commands and pipelines (for process substitution) are supported."
    cmd1 = sh("echo")
    pipe1 = sh("echo") | sh("cat")
    cmd = sh("echo", cmd1, pipe1)
    assert cmd.args == ("echo", cmd1, pipe1)


def test_stringio():
    "StringIO is handled correctly."
    cmd = sh("echo", StringIO("abc"))
    assert cmd.args == ("echo", "abc")


def test_bytesio():
    "BytesIO is handled correctly."
    cmd = sh("echo", BytesIO(b"abc"))
    assert cmd.args == ("echo", b"abc")


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
    "Test the built-in reversed function."
    cmd = sh("echo", reversed((1, 2, 3)))
    assert cmd.args == ("echo", "3", "2", "1")


def test_list():
    "Lists are flattened recursively."
    cmd = sh("echo", [[1, 2], 3])
    assert cmd.args == ("echo", "1", "2", "3")


def test_generator():
    "Generators are flattened recursively."

    def agen(num):
        yield from range(1, num + 1)

    with pytest.raises(TypeError, match="not supported"):
        sh("echo", agen(3))


def test_range():
    "Test the built-in range function."
    with pytest.raises(TypeError, match="not supported"):
        sh("echo", range(3))


def test_enumerate():
    "Test the built-in enumerate function."
    cmd = sh("echo", enumerate(["a", "b", "c"]))
    assert cmd.args == ("echo", "0", "a", "1", "b", "2", "c")


def test_zip():
    "Test the built-in zip function."
    names = ["-a", "-b", "-c"]
    cmd = sh("echo", zip(names, (1, 2, 3)))
    assert cmd.args == ("echo", "-a", "1", "-b", "2", "-c", "3")


def test_dict():
    "Test a dictionary."
    with pytest.raises(TypeError, match="not supported"):
        sh("echo", dict(a=1))


def test_set():
    "Test a set."
    with pytest.raises(TypeError, match="not supported"):
        sh("echo", {1, 2, 3})


def test_frozenset():
    "Test a frozenset."
    with pytest.raises(TypeError, match="not supported"):
        sh("echo", frozenset([1, 2, 3]))


def test_sorted_set():
    "Test the result of the sorted function."
    cmd = sh("echo", sorted({3, 1, 2}))
    assert cmd.args == ("echo", "1", "2", "3")


class _NoRepr:
    pass


def test_class():
    "Test a class without the default __repr__ and __str__."
    cmd = sh("echo", _NoRepr())
    assert _str_not_implemented(cmd)


def test_function():
    "Test a function."
    cmd = sh("echo", test_class)
    assert _str_not_implemented(cmd)


def test_mapping():
    "Test a custom mapping class."
    with pytest.raises(TypeError, match="not supported"):
        sh("echo", _TestMap(a=1, b=2))


def test_iterable():
    "Test a custom iterable."
    cmd = sh("echo", _TestIterable(1, 2, 3))
    assert _str_not_implemented(cmd)


def test_iterator():
    "Test a custom iterator."
    cmd = sh("echo", _TestIterator(1, 2, 3))
    assert _str_not_implemented(cmd)


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


def _coerce_dict(arg):
    if isinstance(arg, dict):
        return [(f"--{k}", v) for k, v in arg.items()]
    return arg


def test_coerce_arg_dict():
    "Test `coerce_arg` option with a dictionary."
    sh1 = sh.set(coerce_arg=_coerce_dict)
    cmd = sh1("echo", {"a": 1, "b": 2})
    assert cmd.args == ("echo", "--a", "1", "--b", "2")


def test_coerce_arg_shlex():
    "Test coerce_arg setting with shlex.split function."
    sh1 = sh.set(coerce_arg=shlex.split)
    cmd = sh1('echo -x --a 1 --b 2 "some file"')
    assert cmd.args == ("echo", "-x", "--a", "1", "--b", "2", "some file")
