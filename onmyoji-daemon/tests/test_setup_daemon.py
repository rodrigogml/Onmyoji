from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path


def setup_module():
    path = Path(__file__).parents[1] / "setupDaemon.py"
    spec = importlib.util.spec_from_file_location("setup_daemon", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_voice_setup_authorizes_only_telegram_staging(tmp_path):
    module = setup_module(); workspace = tmp_path / "workspace"; workspace.mkdir()
    configs = tmp_path / "configs"; configs.mkdir()
    (configs / "onmyoji-system.toml").write_text('[codex]\nmodel = "gpt-5.6-terra"\nmodel_reasoning_effort = "medium"\nproject_directory = "' + workspace.as_posix() + '"\nsandbox_mode = "workspace-write"\napproval_policy = "never"\nadditional_writable_directories = []\n', encoding="utf-8")
    (configs / "eccovox.toml").write_text('[defaults]\nrequest_timeout_seconds = 120\nmax_audio_bytes = 100\nmax_text_characters = 100\n\n[profiles.eccovox]\nbase_url = "http://127.0.0.1:8870"\nreadable_roots = []\nwritable_roots = []\n', encoding="utf-8")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    target = module.bootstrap(tmp_path); data = module.telegram_data(tmp_path); module.save_telegram(tmp_path, "vault", "APIs/Telegram:Bot", data)

    staging = module.save_voice_reply(tmp_path, True, "eccovox", 15, True)

    assert staging == workspace / ".onmyoji" / "telegram" / "staging" and staging.is_dir()
    ecco = tomllib.loads((configs / "eccovox.toml").read_text(encoding="utf-8"))["profiles"]["eccovox"]
    assert ecco["readable_roots"] == [str(staging)] and ecco["writable_roots"] == [str(staging)]
    system = tomllib.loads((configs / "onmyoji-system.toml").read_text(encoding="utf-8"))["codex"]
    assert system["additional_writable_directories"] == [str(staging)]
    assert tomllib.loads(target.read_text(encoding="utf-8"))["voice_reply"]["enabled"]
