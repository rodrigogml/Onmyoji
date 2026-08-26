---
name: aws
description: Operate Amazon Web Services through the installed AWS CLI with credentials read exclusively from KeePassVault. Use when listing or managing Amazon S3 buckets and objects, or validating AWS identity with STS, through a project-specific INI profile.
---

# AWS CLI

Use `scripts/aws.py --config <profile.ini>` as the only interface. Send one JSON request with `version: 1` through stdin and read one JSON response from stdout.

Use a real profile stored outside the repository. Generate it from `configs/aws.example.ini`; do not put credentials in it.

The Vault entry stores `AWS_ACCESS_KEY_ID` in `username` and `AWS_SECRET_ACCESS_KEY` in `password`. Read the identity with `identity.get` before a meaningful operation and configure `expected_account_id` to prevent a wrong-account action.

The wrapper runs the installed AWS CLI with isolated process environment variables, empty temporary AWS config and credentials files, and EC2 instance metadata disabled. Do not call `aws.exe` directly for authenticated operations.

Supported operations and their parameters are in [references/api-contracts.md](references/api-contracts.md). Configuration and credential-provider contracts are in [references/configuration.md](references/configuration.md) and [references/keepass-provider.md](references/keepass-provider.md).

Every upload, download, copy, and deletion requires `confirm: true`. A download that would replace an existing local file also requires `overwrite: true`.

