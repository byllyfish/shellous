"Unit tests for the Command class."

from pathlib import Path

import pytest
from immutables import Map as ImmutableDict
from shellous import DEVNULL, context
from shellous.command import Options


@pytest.fixture
def sh():
    return context()


def test_invalid(sh):
    "Calling sh() with 0 arguments is invalid."
    with pytest.raises(ValueError, match="Command must include program name"):
        sh()


def test_invalid_empty_args(sh):
    "Calling sh() with 0 arguments is invalid."
    with pytest.raises(ValueError, match="Command must include program name"):
        sh([], ())


def test_args(sh):
    "Test command args coercion."
    cmd = sh("echo", "a", 2)
    assert cmd.args == ("echo", "a", "2")


def test_name(sh):
    "Test command's name property."
    cmd = sh("echo", "a")
    assert cmd.name == "echo"


def test_name_long(sh):
    "Test command's name property with long name."
    cmd = sh("/venv-123456789/name_longer_than_many_names")
    assert cmd.name == "...789/name_longer_than_many_names"


def test_apply_concat(sh):
    "You can apply an arglist to an existing command."
    cmd = sh("echo", "-n")
    cmd2 = cmd("a", "b")
    assert cmd2.args == ("echo", "-n", "a", "b")


def test_apply_noop(sh):
    "It's a noop to apply an empty arglist to a command."
    cmd = sh("echo")
    assert cmd() is cmd
    assert cmd()() is cmd


def test_str(sh):
    "Command can be coerced to string."
    cmd = sh("/bin/echo", "-n", "secret").env(SECRET=42)
    assert str(cmd) == "/bin/echo"


def test_repr(sh):
    "Command supplies a __repr__ implementation."
    cmd = sh("echo", "-n", "secret_arg").env(SECRET=42)
    result = repr(cmd)
    assert result.startswith(
        "Command(args=('echo', '-n', 'secret_arg'), options=Options("
    )

    # Env vars are not included in output for security reasons.
    assert "SECRET" not in result


def test_non_existant(sh):
    "Commands can be created with a bogus program name."
    cmd = sh("/bogus/zzz")
    assert cmd.args == ("/bogus/zzz",)


def test_noexpand_glob(sh):
    "Glob * is not supported."
    cmd = sh("echo", "*")
    assert cmd.args == ("echo", "*")


def test_noexpand_variable(sh):
    "Expanding environment variables is not supported."
    cmd = sh("echo", "$PATH")
    assert cmd.args == ("echo", "$PATH")


def test_tuple_arg(sh):
    "Command may include tuple arguments."
    cmd = sh("echo", ("-n", "arg1", "arg2"))
    assert cmd.args == ("echo", "-n", "arg1", "arg2")


def test_nested_list_arg(sh):
    "Test a command that includes nested lists in its arguments."
    cmd = sh(
        "echo",
        ["-n", ["arg1"]],
        list(range(1, 4)),
        [1 + 3j],
    )
    assert cmd.args == ("echo", "-n", "arg1", "1", "2", "3", "(1+3j)")


def test_none_arg(sh):
    "Test passing None as an argument."
    with pytest.raises(TypeError):
        sh("echo", None)


def test_command_as_arg(sh):
    """Test passing a command as an argument to another command.

    This syntax is reserved for process substitution. The default is to
    read from the process stdout (mode='r'). The alternative is to write to
    the process stdin (mode='w'). We will need an option to disable process
    substitution for commands like 'sudo' where we want to insert the
    command's arguments."""
    with pytest.raises(NotImplementedError, match="reserved"):
        # same as `cat <(ls)`
        sh("cat", sh("ls"))


def test_ellipsis_as_arg(sh):
    """Test passing Ellipsis as an argument.

    This syntax is reserved for argument insertion."""
    with pytest.raises(NotImplementedError, match="reserved"):
        sh("ls", ..., "some_file")


def test_dict_arg(sh):
    """Test passing a dictionary as an argument.

    This syntax is reserved for dict args feature.
    """
    with pytest.raises(NotImplementedError, match="reserved"):
        sh("echo", dict(a="b"))


def test_set_arg(sh):
    """Test passing a set as an argument.

    This syntax is reserved.
    """
    with pytest.raises(NotImplementedError, match="reserved"):
        sh("echo", {0})


def test_bytearray_arg(sh):
    "Test passing a bytearray as an argument."
    cmd = sh("echo", bytearray("abc", "utf-8"))
    assert cmd.args == ("echo", b"abc")


def test_command_hash_eq(sh):
    "Test that a command is hashable."
    cmd1 = sh("echo").env(FOO=1)
    cmd2 = sh("echo").env(FOO=1)
    assert hash(cmd1) is not None
    assert hash(cmd1) == hash(cmd2)
    assert cmd1 == cmd2

    cmd3 = sh("echo").env(FOO=2)
    assert cmd3 != cmd1


def test_command_env_init(sh):
    "Test the environment handling of the Command class."
    cmd1 = sh("echo")
    assert cmd1.options.env is None

    sh = sh.env(A=1)
    cmd2 = sh("echo")
    assert cmd2.options.env == ImmutableDict(A="1")

    cmd3 = cmd2.env(B=2)
    assert cmd3.options.env == ImmutableDict(A="1", B="2")


def test_options_merge_env(sh):
    "Test the internal Options class `merge_env` method."
    opts1 = Options(sh)
    opts2 = opts1.set_env(dict(A=1))
    opts3 = opts2.set(dict(inherit_env=False))
    assert opts3.context is sh

    env1 = opts1.merge_env()
    assert env1 is None

    env2 = opts2.merge_env()
    assert isinstance(env2, dict)
    assert "PATH" in env2
    assert env2["A"] == "1"

    env3 = opts3.merge_env()
    assert env3 == dict(A="1")

    sh2 = sh.env(B=2)
    assert sh2 is not sh
    assert sh2.options.env == ImmutableDict(B="2")

    # Copying env from `Context` is done by `Command`...
    opts4 = Options(sh2)
    assert opts4.context is sh2
    assert opts4.env is None


def test_options_hash_eq(sh):
    "Test that the internal Options class is hashable."
    opts1 = Options(sh)
    opts2 = opts1.set_env(dict(A=1))
    opts3 = opts2.set(dict(inherit_env=False))

    assert hash(opts1) is not None
    assert hash(opts2) is not None
    assert hash(opts3) is not None

    assert opts1 != opts2
    assert opts2 != opts3


def test_arg_checks_append(sh):
    "Test that redirections with `append=True` use the allowed types."

    echo = sh("echo")
    echo.stdout("/tmp/tmp_test_file", append=True)
    echo.stdout(b"/tmp/tmp_test_file", append=True)
    echo.stdout(Path("/tmp/tmp_test_file"), append=True)

    with pytest.raises(TypeError, match="append"):
        echo.stdout(7, append=True)  # 7 is file descriptor

    with pytest.raises(TypeError, match="append"):
        echo.stdout(DEVNULL, append=True)
