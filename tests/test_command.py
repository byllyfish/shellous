"Unit tests for the Command class."

# pylint: disable=redefined-outer-name,invalid-name

import dataclasses
import pickle
from pathlib import Path

import pytest

from shellous import sh
from shellous.command import Options


def test_invalid():
    "Calling sh() with 0 arguments is invalid."
    with pytest.raises(ValueError, match="Command must include program name"):
        sh()


def test_invalid_empty_args():
    "Calling sh() with 0 arguments is invalid."
    with pytest.raises(ValueError, match="Command must include program name"):
        sh([], ())


def test_args():
    "Test command args coercion."
    cmd = sh("echo", "a", 2)
    assert cmd.args == ("echo", "a", "2")


def test_args2():
    "Test command args coercion and flattening."
    cmd = sh(["echo", "b", [-1]])
    assert cmd.args == ("echo", "b", "-1")


def test_name():
    "Test command's name property."
    cmd = sh("echo", "a")
    assert cmd.name == "echo"


def test_name_long():
    "Test command's name property with long name."
    cmd = sh("/venv-123456789/name_longer_than_many_names")
    assert cmd.name == "...789/name_longer_than_many_names"


def test_alt_name():
    "Test command's name property with alt_name option."
    cmd = sh("echo", "a").set(alt_name="my-echo-a")
    assert cmd.name == "my-echo-a"


def test_apply_concat():
    "You can apply an arglist to an existing command."
    cmd = sh("echo", "-n")
    cmd2 = cmd("a", "b")
    assert cmd2.args == ("echo", "-n", "a", "b")


def test_apply_noop():
    "It's a noop to apply an empty arglist to a command."
    cmd = sh("echo")
    assert cmd() is cmd
    assert cmd()() is cmd


def test_str():
    "Command can be coerced to string."
    cmd = sh("/bin/echo", "-n", "secret").env(SECRET=42)
    assert str(cmd) == "/bin/echo"


def test_repr():
    "Command supplies a __repr__ implementation."
    cmd = sh("echo", "-n", "secret_arg").env(SECRET=42)
    result = repr(cmd)
    assert result.startswith(
        "Command(args=('echo', '-n', 'secret_arg'), options=Options("
    )

    # Env vars are not included in output for security reasons.
    assert "SECRET" not in result


def test_non_existant():
    "Commands can be created with a bogus program name."
    cmd = sh("/bogus/zzz")
    assert cmd.args == ("/bogus/zzz",)


def test_noexpand_glob():
    "Glob * is not supported."
    cmd = sh("echo", "*")
    assert cmd.args == ("echo", "*")


def test_noexpand_variable():
    "Expanding environment variables is not supported."
    cmd = sh("echo", "$PATH")
    assert cmd.args == ("echo", "$PATH")


def test_tuple_arg():
    "Command may include tuple arguments."
    cmd = sh("echo", ("-n", "arg1", "arg2"))
    assert cmd.args == ("echo", "-n", "arg1", "arg2")


def test_nested_list_arg():
    "Test a command that includes nested lists in its arguments."
    cmd = sh(
        "echo",
        ["-n", ["arg1"]],
        list(range(1, 4)),
        [1 + 3j],
    )
    assert cmd.args == ("echo", "-n", "arg1", "1", "2", "3", "(1+3j)")


def test_none_arg():
    "Test passing None as an argument."
    with pytest.raises(TypeError):
        sh("echo", None)


def test_ellipsis_as_arg():
    """Test passing Ellipsis as an argument.

    This syntax is reserved for argument insertion."""
    with pytest.raises(NotImplementedError, match="reserved"):
        sh("ls", ..., "some_file")


def test_dict_arg():
    """Test passing a dictionary as an argument.

    This syntax is reserved for dict args feature.
    """
    with pytest.raises(NotImplementedError, match="reserved"):
        sh("echo", dict(a="b"))


def test_set_type_as_arg():
    """Test passing a set as an argument.

    This syntax is reserved.
    """
    with pytest.raises(NotImplementedError, match="reserved"):
        sh("echo", {0})


def test_bytearray_arg():
    "Test passing a bytearray as an argument."
    cmd = sh("echo", bytearray("abc", "utf-8"))
    assert cmd.args == ("echo", b"abc")


def test_command_hash_eq():
    "Test that a command is hashable."
    cmd1 = sh("echo").env(FOO=1)
    cmd2 = sh("echo").env(FOO=1)
    assert hash(cmd1) is not None
    assert hash(cmd1) == hash(cmd2)
    assert cmd1 == cmd2

    cmd3 = sh("echo").env(FOO=2)
    assert cmd3 != cmd1


def test_command_env_init():
    "Test the environment handling of the Command class."
    cmd1 = sh("echo")
    assert cmd1.options.env is None

    ash = sh.env(A=1)
    cmd2 = ash("echo")
    assert cmd2.options.env == dict(A="1")

    cmd3 = cmd2.env(B=2)
    assert cmd3.options.env == dict(A="1", B="2")


def test_options_merge_env():
    "Test the internal Options class `merge_env` method."
    opts1 = Options()
    assert opts1.env is None
    opts2 = opts1.set_env(dict(A=1))
    opts3 = opts2.set(dict(inherit_env=False))

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
    assert sh2.options.env == dict(B="2")


def test_options_hash_eq():
    "Test that the internal Options class is hashable."
    opts1 = Options()
    opts2 = opts1.set_env(dict(A=1))
    opts3 = opts2.set(dict(inherit_env=False))

    assert hash(opts1) is not None
    assert hash(opts2) is not None
    assert hash(opts3) is not None

    assert opts1 != opts2
    assert opts2 != opts3


def test_replace_args_method():
    "Test the Command set_args method."
    echo = sh("echo", 1, 2, 3)
    cmd = echo._replace_args(("echo", "4", "5"))
    assert cmd == sh("echo", "4", "5")

    # _replace_args does not stringify anything.
    cmd = cmd._replace_args(("echo", 6))
    assert cmd.args == ("echo", 6)
    assert cmd.options == echo.options


def test_percent_op_not_implemented():
    "Test the percent/modulo operator for concatenation."
    echo = sh("echo", "hello")
    with pytest.raises(TypeError):
        assert None % echo  # pyright: ignore[reportGeneralTypeIssues]
    with pytest.raises(TypeError):
        assert echo % None  # pyright: ignore[reportGeneralTypeIssues]


def test_command_pickle():
    "Test that basic commands can be pickled."
    cmd = sh("echo", "hello") | Path("/tmp/test_file")
    value = pickle.dumps(cmd)
    result = pickle.loads(value)

    # Compare commands.
    assert result is not cmd
    assert result == cmd


def test_command_pickle_callback():
    "Test that some settings can't be pickled."

    def _callback(*_ignore):
        pass

    cmd = sh("echo", "hello").set(audit_callback=_callback)

    with pytest.raises((pickle.PicklingError, AttributeError)):
        pickle.dumps(cmd)


def test_dataclasses():
    """Test that data classes have the expected fields.

    Check that class variables don't appear here.
    """
    ctxt_fields = [field.name for field in dataclasses.fields(sh)]
    assert sorted(ctxt_fields) == ["options"]

    cmd_fields = [field.name for field in dataclasses.fields(sh("echo"))]
    assert sorted(cmd_fields) == ["args", "options"]

    opt_fields = [field.name for field in dataclasses.fields(sh.options)]
    assert sorted(opt_fields) == [
        "_catch_cancelled_error",
        "_preexec_fn",
        "_return_result",
        "_start_new_session",
        "_writable",
        "alt_name",
        "audit_callback",
        "cancel_signal",
        "cancel_timeout",
        "close_fds",
        "encoding",
        "env",
        "error",
        "error_append",
        "error_close",
        "exit_codes",
        "inherit_env",
        "input",
        "input_close",
        "output",
        "output_append",
        "output_close",
        "pass_fds",
        "pass_fds_close",
        "path",
        "pty",
        "timeout",
    ]


def test_context_enums():
    "Test that `sh` defines the important Redirect enums."
    assert sh.CAPTURE.name == "CAPTURE"
    assert sh.DEVNULL.name == "DEVNULL"
    assert sh.INHERIT.name == "INHERIT"
    assert sh.STDOUT.name == "STDOUT"


def test_context_result():
    "Test that `sh` supports the .result modifier."
    ctxt = sh.result
    assert ctxt.options._return_result
    assert ctxt.options.exit_codes == range(-255, 256)


def test_command_invalid_encoding():
    "Test the empty encoding is invalid."
    with pytest.raises(TypeError, match="invalid encoding"):
        sh("echo").set(encoding="")

    with pytest.raises(TypeError, match="invalid encoding"):
        sh("echo").set(encoding=None)  # pyright: ignore[reportGeneralTypeIssues]


def test_context_invalid_encoding():
    "Test context with invalid encoding."
    with pytest.raises(TypeError, match="invalid encoding"):
        sh.set(encoding="")

    with pytest.raises(TypeError, match="invalid encoding"):
        sh.set(encoding=None)  # pyright: ignore[reportGeneralTypeIssues]


def test_context_redirects():
    "Test that `sh` supports stdin/stdout/stderr methods."
    out1 = bytearray()
    out2 = bytearray()
    ctxt = sh.stdin("abc").stdout(out1).stderr(out2)

    assert ctxt.options.input == "abc"
    assert ctxt.options.output == out1
    assert ctxt.options.error == out2

    assert not ctxt.options.input_close
    assert not ctxt.options.output_close
    assert not ctxt.options.error_close


def test_context_redirects_invalid():
    "Test that `sh` complains about invalid redirects."
    with pytest.raises(TypeError):
        sh.stdin(None)
    with pytest.raises(ValueError):
        sh.stdin(sh.STDOUT)

    with pytest.raises(TypeError):
        sh.stdout(None)
    with pytest.raises(ValueError):
        sh.stdout(sh.STDOUT)

    with pytest.raises(TypeError):
        sh.stderr(None)


def test_command_append():
    "Test command appending an invalid type."
    with pytest.raises(TypeError, match=">>"):
        _ = sh("echo") >> (1 + 2j)  # pyright: ignore[reportGeneralTypeIssues]
