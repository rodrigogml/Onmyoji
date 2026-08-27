# KeePassVault provider

Invoke the configured `skillKeePassVault/scripts/keepass_vault.py` subprocess twice: first for the configured entry's `username`, then its `password`. Pass `version: 1`, `operation: read`, the configured entry path, and the parsed `auth_json` through stdin.

Keep both returned values only in memory. Never put either value in command-line arguments, files, logs, exception messages, or the JSON response from this skill.

