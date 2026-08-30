# Configuration

Create `<CODEX_HOME>/configs/aws.toml` from `configs/aws.toml.model` and call the wrapper with `--config <CODEX_HOME>/configs/aws.toml --profile <name>`.

`[defaults]` accepts `timeout_seconds` and `max_attempts`. Each `[profiles.<name>]` accepts `cli_path` (optional; defaults to `aws`), `region`, `expected_account_id` (optional 12-digit AWS account ID), `vault_profile`, and `vault_entry_path`.

The configured KeePass entry uses `username` for the Access Key ID and `password` for the Secret Access Key. This version supports permanent IAM access keys only; do not store an AWS session token in the profile.
