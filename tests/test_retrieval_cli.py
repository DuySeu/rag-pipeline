from __future__ import annotations

import pytest

import main as cli
from core import CliError, ErrorKind
from retrieval.pipeline import Answer


def run_cli(monkeypatch, result):
    def fake_ask(*args):
        if isinstance(result, CliError):
            raise result
        return [], result

    monkeypatch.setattr("sys.argv", ["main.py", "ask", "q"])
    for name in ("OPENROUTER_API_KEY", "EMBED_MODEL", "CHAT_MODEL"):
        monkeypatch.setenv(name, "fake")
    monkeypatch.setattr(cli, "make_client", lambda settings: None)
    monkeypatch.setattr(cli, "make_embedder", lambda *args: None)
    monkeypatch.setattr(cli, "open_collection", lambda settings: None)
    monkeypatch.setattr("retrieval.pipeline.ask", fake_ask)
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    return exit_info.value.code


def test_cli_prints_answer_label_sources_and_exits_zero(monkeypatch, capsys):
    answer = Answer("answer", "twelve days [1]", citations=[("leave.md", None), ("t.pdf", 2)])

    assert run_cli(monkeypatch, answer) == 0

    assert capsys.readouterr().out == (
        "[ANSWER]\ntwelve days [1]\n\nSources:\n- leave.md\n- t.pdf (2)\n"
    )


def test_cli_prints_clarify_without_sources_and_exits_three(monkeypatch, capsys):
    answer = Answer("clarify", "Which policy?", citations=[("leave.md", None)])

    assert run_cli(monkeypatch, answer) == 3

    assert capsys.readouterr().out == "[CLARIFY]\nWhich policy?\n"


def test_cli_prints_refuse_and_exits_four(monkeypatch, capsys):
    assert run_cli(monkeypatch, Answer("refuse", "none")) == 4

    assert capsys.readouterr().out == "[REFUSE]\nnone\n"


@pytest.mark.parametrize("kind", list(ErrorKind))
def test_cli_prints_error_label_to_stderr_and_exits_with_its_code(monkeypatch, capsys, kind):
    assert run_cli(monkeypatch, CliError(kind, "boom")) == kind.value

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"[ERROR {kind.name}] boom\n"
