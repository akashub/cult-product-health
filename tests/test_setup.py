from cultph import setup


def test_env_file_roundtrip_and_permissions(tmp_path):
    f = tmp_path / ".env"
    setup.write_env_file({"A": "1", "B": "", "C": "x=y"}, f)
    assert setup.read_env_file(f) == {"A": "1", "C": "x=y"}
    assert oct(f.stat().st_mode)[-3:] == "600"


def test_run_setup_keeps_sets_and_clears(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    monkeypatch.setattr(setup, "ENV_FILE", f)
    setup.write_env_file({"ANTHROPIC_API_KEY": "old", "SLACK_WEBHOOK_URL": "hook"})
    answers = {"Anthropic": "", "Slack": "-", "Telegram bot": "tok"}   # keep, clear, set
    setup.run_setup(ask=lambda prompt: next((v for k, v in answers.items() if prompt.startswith(k)), ""))
    assert setup.read_env_file() == {"ANTHROPIC_API_KEY": "old", "TELEGRAM_BOT_TOKEN": "tok"}
