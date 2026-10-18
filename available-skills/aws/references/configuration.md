# Configuration

Create `<CODEX_HOME>/configs/aws.toml` from `configs/aws.toml.model` and call the wrapper with `--config <CODEX_HOME>/configs/aws.toml --profile <name>`.

`[defaults]` accepts `timeout_seconds` and `max_attempts`. Each `[profiles.<name>]` accepts `cli_path` (optional; defaults to `aws`), `region` (optional default region; may be empty or omitted), `expected_account_id` (optional 12-digit AWS account ID), `vault_profile`, and `vault_entry_path`.

The configured KeePass entry uses `username` for the Access Key ID and `password` for the Secret Access Key. This version supports permanent IAM access keys only; do not store an AWS session token in the profile.

The setup creates new profiles with `region = ""` unless a default is entered. To clear an existing default, edit the region and submit an empty value, or use `setupSkill.py --action profile-update --profile <name> --set region=`. Existing profiles keep their configured region.

> [!IMPORTANT]
> With an empty or omitted profile region, every wrapper request must include a non-empty JSON `region`. A request region overrides the profile for that operation without changing the saved configuration. The profile identity test sends no override and therefore requires a default region; use an explicit-region `identity.get` request to test a profile without one.
