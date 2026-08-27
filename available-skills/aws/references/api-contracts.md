# API contracts

Every request includes `version: 1` and `operation`. Successful responses contain `version`, `ok: true`, `operation`, and `data`; errors contain `ok: false` and a stable error code.

| Operation | Required fields | Effect |
| --- | --- | --- |
| `identity.get` | none | Returns STS caller identity and validates `expected_account_id` when configured. |
| `s3.bucket.list` | none | Lists buckets available to the credential. |
| `s3.bucket.location` | `bucket` | Returns the bucket Region. |
| `s3.object.list` | `bucket` | Lists objects. Optional: `prefix`, `continuation_token`, `max_keys` (1–1000). |
| `s3.object.head` | `bucket`, `key` | Returns object metadata. |
| `iam.role.list` | none | Lists IAM roles available to the credential. |
| `s3.object.upload` | `bucket`, `key`, `source`, `confirm: true` | Creates or replaces an object. |
| `s3.object.download` | `bucket`, `key`, `destination`, `confirm: true` | Writes a local file; add `overwrite: true` if it exists. |
| `s3.object.download.batch` | `bucket`, `manifest`, `destination`, `status_path`, `failures_path`, `workers` (1–32), `confirm: true` | Downloads the tab-delimited key manifest concurrently. Each successful file is atomically placed at its final name; failures are recorded for a later retry. |
| `s3.object.copy` | `bucket`, `key`, `source_bucket`, `source_key`, `confirm: true` | Creates or replaces the destination object. |
| `s3.object.delete` | `bucket`, `key`, `confirm: true` | Deletes the current object version or adds a delete marker in a versioned bucket. |

Example read request:

```json
{"version":1,"operation":"s3.object.list","bucket":"example-bucket","prefix":"exports/","max_keys":100}
```

Example write request:

```json
{"version":1,"operation":"s3.object.upload","bucket":"example-bucket","key":"exports/report.csv","source":"C:\\exports\\report.csv","confirm":true}
```

