"Unit tests for Pipeline class."

# pylint: disable=redefined-outer-name,invalid-name

import io
import logging
from pathlib import Path

import pytest
import shellous
from shellous import Pipeline, sh
from shellous.command import Command


def test_empty_pipeline():
    with pytest.raises(ValueError, match="must include at least one command"):
        Pipeline.create()


def test_pipeline_cmd():
    pipe = Pipeline.create(sh("cmd1"), sh("cmd2"))
    assert pipe.commands == (sh("cmd1"), sh("cmd2"))


def test_pipeline_name():
    pipe = Pipeline.create(sh("cmd1"), sh("cmd2"))
    assert pipe.name == "cmd1|cmd2"


def test_pipeline_cmd_append():
    pipe = Pipeline.create(sh("cmd1").stdin("a"), sh("cmd2").stdout("b", append=True))
    assert pipe.commands == (sh("cmd1").stdin("a"), sh("cmd2").stdout("b", append=True))


def test_pipeline():
    pipe = Pipeline.create(sh("echo")) | sh("cat")
    assert pipe.commands == (sh("echo"), sh("cat"))


def test_pipeline_unsupported_rhs():
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for \|"):
        _ = Pipeline.create(sh("echo")) | (1 + 2j)


def test_pipeline_unsupported_lhs():
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for \|"):
        _ = (1 + 2j) | Pipeline.create(sh("echo"))


def test_pipeline_unsupported_rhs_append():
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for >>"):
        _ = Pipeline.create(sh("echo")) >> (1 + 2j)


def test_pipeline_input():
    pipe = "random input" | Pipeline.create(sh("echo"))
    assert pipe.commands == (sh("echo").stdin("random input"),)


def test_pipeline_output():
    pipe = Pipeline.create(sh("echo")) | "/tmp/somefile"
    assert pipe.commands == (sh("echo").stdout("/tmp/somefile"),)


def test_pipeline_output_append():
    pipe = Pipeline.create(sh("echo")) >> "/tmp/somefile"
    assert pipe.commands == (sh("echo").stdout("/tmp/somefile", append=True),)


def test_pipeline_full():
    "Test operator overloading in Pipeline only."
    pipe = (
        "/tmp/input"
        | Pipeline.create(sh("cmd1"))
        | sh("cmd2")
        | Pipeline.create(sh("cmd3")) >> "/tmp/output"
    )
    assert pipe.commands == (
        sh("cmd1").stdin("/tmp/input"),
        sh("cmd2"),
        sh("cmd3").stdout("/tmp/output", append=True),
    )


def test_pipeline_pieces():
    input_ = "/tmp/input" | Pipeline.create(sh("cmd1"))
    output = Pipeline.create(sh("cmd2")) >> "/tmp/output"
    pipe = input_ | output
    assert pipe.commands == (
        sh("cmd1").stdin("/tmp/input"),
        sh("cmd2").stdout("/tmp/output", append=True),
    )


def test_pipeline_or_eq():
    pipe1 = Pipeline.create(sh("ls"))
    pipe2 = pipe1
    pipe2 |= sh("grep", ".")
    assert pipe2 is not pipe1
    assert pipe1 == Pipeline.create(sh("ls"))
    assert pipe2 == Pipeline.create(sh("ls"), sh("grep", "."))


def test_pipeline_rshift_eq():
    pipe = Pipeline.create(sh("ls"))
    pipe >>= "/tmp/output"
    assert pipe == Pipeline.create(
        sh("ls").stdout("/tmp/output", append=True),
    )


def test_pipeline_stderr():
    pipe = Pipeline.create(sh("ls"), sh("grep"))
    pipe = pipe.stderr("/tmp/output", append=True)

    assert pipe == Pipeline.create(
        sh("ls"), sh("grep").stderr("/tmp/output", append=True)
    )


# The following tests depend on operator overloading in Command.


def test_pipeline_full_commands():
    "Test depends on operator overloading in Command."
    pipe = (
        "/tmp/input"
        | sh("cmd1")
        | sh("cmd2")
        | sh("cmd3")
        | sh("cmd4") >> "/tmp/output"
    )
    assert pipe == Pipeline.create(
        sh("cmd1").stdin("/tmp/input"),
        sh("cmd2"),
        sh("cmd3"),
        sh("cmd4").stdout("/tmp/output", append=True),
    )


def test_pipeline_or_eq_commands():
    pipe = sh("ls")
    pipe |= sh("grep")
    assert pipe == Pipeline.create(sh("ls"), sh("grep"))


def test_pipeline_rshift_eq_commands():
    pipe = sh("ls") | sh("grep")
    pipe >>= "/tmp/output"
    assert pipe == Pipeline.create(
        sh("ls"),
        sh("grep").stdout("/tmp/output", append=True),
    )


def test_pipeline_or_eq_input_commands():
    pipe = "/tmp/input"
    pipe |= sh("grep")
    pipe |= "/tmp/output"
    assert pipe == sh("grep").stdin("/tmp/input").stdout("/tmp/output")


def test_pipeline_path_input():
    pipe = Path("/tmp/input") | sh("wc")
    assert pipe == sh("wc").stdin(Path("/tmp/input"))


def test_pipeline_path_output():
    pipe = sh("wc") | Path("/tmp/output")
    assert pipe == sh("wc").stdout(Path("/tmp/output"))


def test_pipeline_vs_command():
    """In the current implementation, commands and pipelines are the same."""
    cmd1 = sh("echo").stdin("abc")
    cmd2 = "abc" | sh("echo")  # single command pipeline
    assert cmd1 == cmd2
    assert isinstance(cmd1, Command)
    assert isinstance(cmd2, Command)


def test_pipeline_call():
    "You cannot call a pipeline with 1 or more arguments."
    pipe = sh("echo") | sh("grep")
    assert pipe() is pipe  # no args is okay
    with pytest.raises(TypeError):
        pipe("foo")


def test_invalid_pipeline_override_stdout():
    """A Pipeline will override existing stdout redirections."""
    echo = sh("echo").stdout("/tmp/tmp_file")
    pipe = echo | sh("grep")
    assert pipe == Pipeline.create(sh("echo").stdout("/tmp/tmp_file"), sh("grep"))


def test_invalid_pipeline_override_stdin():
    """A Pipeline will override existing stdin redirections."""
    grep = sh("grep").stdin("/tmp/tmp_file")
    pipe = sh("echo") | grep
    assert pipe == Pipeline.create(sh("echo"), sh("grep").stdin("/tmp/tmp_file"))


def test_invalid_pipeline_operators():
    "Test >> in the middle of a pipeline."
    pipe = sh("echo") >> "/tmp/tmp_file" | sh("cat")
    assert pipe == Pipeline.create(
        sh("echo").stdout("/tmp/tmp_file", append=True), sh("cat")
    )


def test_pipeline_redirect_stringio():
    "Test use of StringIO in pipeline."
    buf = io.StringIO()
    cmd = sh("echo") | buf
    assert cmd == sh("echo").stdout(buf)


def test_pipeline_redirect_stringio_stdin():
    "Test use of StringIO in pipeline."
    buf = io.StringIO()
    cmd = buf | sh("echo")
    assert cmd == sh("echo").stdin(buf)


def test_pipeline_redirect_logger():
    "Test use of StringIO in pipeline."
    logger = logging.getLogger("test_logger")
    cmd = sh("echo") | logger
    assert cmd == sh("echo").stdout(logger)


def test_pipeline_len_getitem():
    "Test access to individual pipeline commands."
    pipe = sh("cmd1") | sh("cmd2") | sh("cmd3")
    assert len(pipe) == 3
    assert pipe[0] == sh("cmd1")
    assert pipe[1] == sh("cmd2")
    assert pipe[2] == sh("cmd3")
    assert pipe[-1] == sh("cmd3")
    assert pipe[-2] == sh("cmd2")


def test_pipeline_redirect_none_stdin():
    "Test use of None in pipeline."
    cmd = None | sh("echo")
    assert cmd == sh("echo").stdin(sh.DEVNULL)


def test_pipeline_redirect_none_stdout():
    "Test use of None in pipeline."
    cmd = sh("echo") | None
    assert cmd == sh("echo").stdout(sh.DEVNULL)


def test_pipeline_redirect_ellipsis_stdin():
    "Test use of Ellipsis in pipeline."
    cmd = ... | sh("echo")
    assert cmd == sh("echo").stdin(sh.INHERIT)


def test_pipeline_redirect_ellipsis_stdout():
    "Test use of Ellipsis in pipeline."
    cmd = sh("echo") | ...
    assert cmd == sh("echo").stdout(sh.INHERIT)


def test_pipeline_redirect_tuple_stdin():
    "Test use of empty tuple in pipeline."
    cmd = () | sh("echo")
    assert cmd == sh("echo").stdin(sh.CAPTURE)


def test_pipeline_redirect_tuple_stdout():
    "Test use of empty tuple in pipeline."
    cmd = sh("echo") | ()
    assert cmd == sh("echo").stdout(sh.CAPTURE)


def test_pipeline_percent_op():
    "Pipeline does not support percent op for concatenating commands."

    pipe = sh("echo", "abc") | sh("cat")
    with pytest.raises(TypeError):
        _ = sh("nohup") % pipe

    with pytest.raises(TypeError):
        _ = pipe % sh("nohup")


def test_pipeline_percent_precedence():
    "Test operator precedence with % operator."

    cmd = sh("nohup") % sh("echo") >> "/tmp/output"
    assert cmd == sh("nohup", sh("echo").args).stdout("/tmp/output", append=True)

    cmd = "xyz" | sh("nohup") % sh("echo") | sh("cat")
    assert cmd == "xyz" | sh("nohup", sh("echo").args) | sh("cat")
