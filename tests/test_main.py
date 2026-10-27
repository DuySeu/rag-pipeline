from __future__ import annotations

import pytest

import main


def run_main(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    with pytest.raises(SystemExit) as exit_info:
        main.main()
    return exit_info.value.code


def test_main_exits_two_when_command_is_unknown(monkeypatch, capsys):
    assert run_main(monkeypatch, "nope") == 2

    assert "invalid choice: 'nope'" in capsys.readouterr().err


def test_main_exits_two_when_command_is_missing(monkeypatch, capsys):
    assert run_main(monkeypatch) == 2

    assert "the following arguments are required: command" in capsys.readouterr().err


def test_main_ask_exits_two_without_a_question(monkeypatch, capsys):
    assert run_main(monkeypatch, "ask") == 2

    assert "usage: main.py ask [-h] question" in capsys.readouterr().err


def test_main_exits_five_with_error_label_when_env_value_is_empty(monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    assert run_main(monkeypatch, "eval-ingestion") == 5

    assert capsys.readouterr().err.startswith("[ERROR CONFIG] OPENROUTER_API_KEY is empty")
