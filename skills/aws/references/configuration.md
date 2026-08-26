# Configuration

Create a local profile from `configs/aws.example.ini` and keep it outside the repository.

`[aws]` accepts `cli_path` (optional; defaults to `aws`), `region`, `expected_account_id` (optional 12-digit AWS account ID), `timeout_seconds`, and `max_attempts`. `region`, `timeout_seconds`, and `max_attempts` are required.

`[vault]` accepts `command`, `script`, `config`, `entry_path`, and `auth_json`. It is the direct contract of `skillKeePassVault`; its profile path and authentication selector are not credentials.

The AWS entry must use `username` for the Access Key ID and `password` for the Secret Access Key. This first version supports permanent IAM access keys only. Do not store an AWS session token in the profile.

