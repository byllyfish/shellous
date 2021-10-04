"Unit tests for Pipeline class."

# pylint: disable=redefined-outer-name,invalid-name

import io
import logging
from pathlib import Path

import pytest
from shellous import Pipeline, context
from shellous.command import Command


@pytest.fixture
def sh():
    return context()


def test_empty_pipeline(sh):
    with pytest.raises(ValueError, match="must include at least one command"):
        Pipeline.create()


def test_pipeline_cmd(sh):
    pipe = Pipeline.create(sh("cmd1"), sh("cmd2"))
    assert pipe.commands == (sh("cmd1"), sh("cmd2"))


def test_pipeline_name(sh):
    pipe = Pipeline.create(sh("cmd1"), sh("cmd2"))
    assert pipe.name == "cmd1|cmd2"


def test_pipeline_cmd_append(sh):
    pipe = Pipeline.create(sh("cmd1").stdin("a"), sh("cmd2").stdout("b", append=True))
    assert pipe.commands == (sh("cmd1").stdin("a"), sh("cmd2").stdout("b", append=True))


def test_pipeline(sh):
    pipe = Pipeline.create(sh("echo")) | sh("cat")
    assert pipe.commands == (sh("echo"), sh("cat"))


def test_pipeline_unsupported_rhs(sh):
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for \|"):
        Pipeline.create(sh("echo")) | (1 + 2j)


def test_pipeline_unsupported_lhs(sh):
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for \|"):
        _ = (1 + 2j) | Pipeline.create(sh("echo"))


def test_pipeline_unsupported_rhs_append(sh):
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for >>"):
        _ = Pipeline.create(sh("echo")) >> (1 + 2j)


def test_pipeline_input(sh):
    pipe = "random input" | Pipeline.create(sh("echo"))
    assert pipe.commands == (sh("echo").stdin("random input"),)


def test_pipeline_output(sh):
    pipe = Pipeline.create(sh("echo")) | "/tmp/somefile"
    assert pipe.commands == (sh("echo").stdout("/tmp/somefile"),)


def test_pipeline_output_append(sh):
    pipe = Pipeline.create(sh("echo")) >> "/tmp/somefile"
    assert pipe.commands == (sh("echo").stdout("/tmp/somefile", append=True),)


def test_pipeline_full(sh):
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


def test_pipeline_pieces(sh):
    input = "/tmp/input" | Pipeline.create(sh("cmd1"))
    output = Pipeline.create(sh("cmd2")) >> "/tmp/output"
    pipe = input | output
    assert pipe.commands == (
        sh("cmd1").stdin("/tmp/input"),
        sh("cmd2").stdout("/tmp/output", append=True),
    )


def test_pipeline_or_eq(sh):
    pipe1 = Pipeline.create(sh("ls"))
    pipe2 = pipe1
    pipe2 |= sh("grep", ".")
    assert pipe2 is not pipe1
    assert pipe1 == Pipeline.create(sh("ls"))
    assert pipe2 == Pipeline.create(sh("ls"), sh("grep", "."))


def test_pipeline_rshift_eq(sh):
    pipe = Pipeline.create(sh("ls"))
    pipe >>= "/tmp/output"
    assert pipe == Pipeline.create(
        sh("ls").stdout("/tmp/output", append=True),
    )


# The following tests depend on operator overloading in Command.


def test_pipeline_full_commands(sh):
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


def test_pipeline_or_eq_commands(sh):
    pipe = sh("ls")
    pipe |= sh("grep")
    assert pipe == Pipeline.create(sh("ls"), sh("grep"))


def test_pipeline_rshift_eq_commands(sh):
    pipe = sh("ls") | sh("grep")
    pipe >>= "/tmp/output"
    assert pipe == Pipeline.create(
        sh("ls"),
        sh("grep").stdout("/tmp/output", append=True),
    )


def test_pipeline_or_eq_input_commands(sh):
    pipe = "/tmp/input"
    pipe |= sh("grep")
    pipe |= "/tmp/output"
    assert pipe == sh("grep").stdin("/tmp/input").stdout("/tmp/output")


def test_pipeline_path_input(sh):
    pipe = Path("/tmp/input") | sh("wc")
    assert pipe == sh("wc").stdin(Path("/tmp/input"))


def test_pipeline_path_output(sh):
    pipe = sh("wc") | Path("/tmp/output")
    assert pipe == sh("wc").stdout(Path("/tmp/output"))


def test_pipeline_vs_command(sh):
    """In the current implementation, commands and pipelines are the same."""
    cmd1 = sh("echo").stdin("abc")
    cmd2 = "abc" | sh("echo")  # single command pipeline
    assert cmd1 == cmd2
    assert isinstance(cmd1, Command)
    assert isinstance(cmd2, Command)


def test_pipeline_call(sh):
    "You cannot call a pipeline with 1 or more arguments."
    pipe = sh("echo") | sh("grep")
    assert pipe() is pipe  # no args is okay
    with pytest.raises(TypeError):
        pipe("foo")


def test_invalid_pipeline_override_stdout(sh):
    """A Pipeline will override existing stdout redirections."""
    echo = sh("echo").stdout("/tmp/tmp_file")
    pipe = echo | sh("grep")
    assert pipe == Pipeline.create(sh("echo").stdout("/tmp/tmp_file"), sh("grep"))


def test_invalid_pipeline_override_stdin(sh):
    """A Pipeline will override existing stdin redirections."""
    grep = sh("grep").stdin("/tmp/tmp_file")
    pipe = sh("echo") | grep
    assert pipe == Pipeline.create(sh("echo"), sh("grep").stdin("/tmp/tmp_file"))


def test_invalid_pipeline_operators(sh):
    "Test >> in the middle of a pipeline."
    pipe = sh("echo") >> "/tmp/tmp_file" | sh("cat")
    assert pipe == Pipeline.create(
        sh("echo").stdout("/tmp/tmp_file", append=True), sh("cat")
    )


def test_pipeline_redirect_stringio(sh):
    "Test use of StringIO in pipeline."
    buf = io.StringIO()
    cmd = sh("echo") | buf
    assert cmd == sh("echo").stdout(buf)


def test_pipeline_redirect_logger(sh):
    "Test use of StringIO in pipeline."
    logger = logging.getLogger("test_logger")
    cmd = sh("echo") | logger
    assert cmd == sh("echo").stdout(logger)


def test_pipeline_len_getitem(sh):
    "Test access to individual pipeline commands."
    pipe = sh("cmd1") | sh("cmd2") | sh("cmd3")
    assert len(pipe) == 3
    assert pipe[0] == sh("cmd1")
    assert pipe[1] == sh("cmd2")
    assert pipe[2] == sh("cmd3")
    assert pipe[-1] == sh("cmd3")
    assert pipe[-2] == sh("cmd2")
