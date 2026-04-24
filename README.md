# 🐳 Jira Secrets Scanner

Scans Jira issues for exposed secrets — API keys, tokens, passwords, and credentials. Detects 50+ secret types out of the box, with optional TruffleHog patterns to expand coverage to 1500+. Exports findings to `.xlsx`, `.json`, and interactive `.html` reports.

---

## 1. Build

```bash
docker build -t jira-secret-scanner .
```

---

## 2. Configure

Create a `.env` file with your Jira credentials:

```env
JIRA_EMAIL=user@company.com
JIRA_TOKEN=ATATT3xFfGF0your_token_here_xxxxxxxxxxxxxxxxxxxxxxxxxxx
JIRA_URL=https://yourcompany.atlassian.net
```

> Generate your API token at: https://id.atlassian.com/manage-profile/security/api-tokens

---

## 3. Run

### Scan all projects (XLSX output)

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets --output /reports/jira_secrets
```

### Also generate an interactive HTML report

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets --html --output /reports/jira_secrets
```

Produces `jira_secrets.xlsx` and `jira_secrets.html`. Add `--json` to also get `jira_secrets.json`.

### Scan with TruffleHog patterns (1600+ patterns)

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  -v $(pwd)/trufflehog.yaml:/app/trufflehog.yaml:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --trufflehog-patterns /app/trufflehog.yaml \
  --output /reports/jira_secrets
```

Filter detectors by keyword:

```bash
# Include only AWS-related detectors
--trufflehog-patterns /app/trufflehog.yaml -tk aws

# Include aws and api, exclude gateway
--trufflehog-patterns /app/trufflehog.yaml -tk aws,api -tek gateway,arn
```

### Scan specific projects

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --projects PROJ1,PROJ2 \
  --output /reports/jira_secrets
```

### Limit issues per project

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --max-issues 100 \
  --output /reports/jira_secrets
```

### Incremental scan (only new/updated issues)

On the first run a state file is created. Subsequent runs only scan issues updated since the last run — much faster for large Jira instances.

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  -v $(pwd)/.state:/app/.state \
  jira-secret-scanner \
  --env --scan-secrets \
  --incremental \
  --state-file /app/.state/scan_state.json \
  --output /reports/jira_secrets
```

### Parallel scanning (faster for large instances)

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --workers 20 \
  --output /reports/jira_secrets
```

Recommended range: 5–20 workers. Default is 1 (sequential).

### Skip known false positives

Create a `.jira_scanner_ignore` file (auto-loaded if present) or pass a custom path with `--ignore-file`:

```
# Format: ISSUE-KEY:SecretType:SecretValue
# Use * as wildcard to ignore a value across all issues

PROJ-123:AWS Access Key ID:AKIAIOSFODNN7EXAMPLE
*:GitHub Personal Access Token:ghp_testtoken12345678901234567890
```

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  -v $(pwd)/.jira_scanner_ignore:/app/.jira_scanner_ignore:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --ignore-file /app/.jira_scanner_ignore \
  --output /reports/jira_secrets
```

### Filter by date

Three flags control which issues are included based on their dates. They can be used independently or combined.

**Scan issues updated in the last N days** — useful for a weekly cron job:

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --since-days 7 \
  --output /reports/jira_secrets
```

**Scan issues updated on or after a specific date:**

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --since-date 2026-01-01 \
  --output /reports/jira_secrets
```

**Scan issues created on or after a specific date:**

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --created-after 2026-01-01 \
  --output /reports/jira_secrets
```

**Combine filters** — for example, issues created in 2026 that were also touched in the last week:

```bash
  --created-after 2026-01-01 --since-days 7
```

> If `--incremental` is used together with `--since-days` or `--since-date`, the incremental state date takes priority for each project.

---

### Scan attachments

By default, only text fields are scanned. To also scan attached files, add `--scan-attachments`:

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --scan-attachments \
  --output /reports/jira_secrets
```

To skip large files, set a size limit:

```bash
  --scan-attachments --max-attachment-size 5mb
```

### Send report by email

Requires [AWS SES](https://aws.amazon.com/ses/) with a verified sender address. Multiple recipients are supported — separate addresses with commas.

Default region is `eu-central-1`. Override with `--aws-region` if needed.

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  -e AWS_ACCESS_KEY_ID=AKIA... \
  -e AWS_SECRET_ACCESS_KEY=... \
  jira-secret-scanner \
  --env --scan-secrets \
  --output /reports/jira_secrets \
  --email-sender scanner@company.com \
  --email-recipient "security@company.com,ciso@company.com" \
  --aws-region eu-central-1
```

---

### Notify issue authors personally

When `--notify-authors` is set, each Jira issue author receives a **personal email** with only their own findings attached as a separate XLSX file. This is in addition to the general report sent to `--email-recipient`.

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  -e AWS_ACCESS_KEY_ID=AKIA... \
  -e AWS_SECRET_ACCESS_KEY=... \
  jira-secret-scanner \
  --env --scan-secrets \
  --output /reports/jira_secrets \
  --email-sender appsec@company.com \
  --email-recipient security@company.com \
  --notify-authors \
  --notify-domain fozzy.ua,temabit.com
```

Use `--notify-domain` to restrict delivery to specific corporate domains. Authors with addresses outside the allowed domains (e.g. `@gmail.com`) are silently skipped. If `--notify-domain` is omitted, notifications are sent to all author emails — use with caution.

**Email the author receives:**

```
Subject: ⚠️ Security Alert: 3 exposed secrets found in your Jira issues

Hi John Smith,

Our automated security scanner has detected 3 exposed secrets in Jira issues
you created or commented on:

  CSD-142, CSD-198

Please take the following actions immediately:
  1. Revoke / rotate the exposed credentials
  2. Review the attached report for details
  3. Never store secrets in Jira — use a secrets manager (Vault, AWS Secrets Manager, etc.)

The full list of findings is attached as an Excel file.

If you believe this is a false positive, contact the security team.

---
This is an automated message from the Jira Secrets Scanner.
```

---

## What gets scanned?

### By default (`--scan-secrets`)

| Field | Description |
|---|---|
| **Summary** | Issue title |
| **Description** | Issue body (plain text and Atlassian Document Format) |
| **Comments** | All comments on the issue |

### With `--scan-attachments`

Each attachment is downloaded and its text is extracted before scanning.

**Text & code** — decoded directly, no extra dependencies:

| Extensions | |
|---|---|
| `txt` `log` `md` `rst` | Plain text and docs |
| `json` `yaml` `yml` `toml` `xml` `csv` | Data and config |
| `env` `conf` `cfg` `ini` `properties` | Environment and config files |
| `py` `sh` `bash` `bat` `ps1` | Scripts |
| `js` `ts` `java` `go` `rb` `php` `cs` `c` `cpp` | Source code |
| `sql` `tf` `hcl` `dockerfile` `html` `css` | Infrastructure and web |

**Documents:**

| Format | Extension | Method |
|---|---|---|
| Word | `docx` | python-docx |
| PDF | `pdf` | PyMuPDF |

**Images (OCR):**

| Extensions | Engine |
|---|---|
| `png` `jpg` `jpeg` `gif` `bmp` `tiff` | Tesseract (English + Russian) |

> OCR works best on clean screenshots with readable text. Handwritten or stylized fonts may produce inaccurate results.

Files with any other extension are silently skipped.

---

## Reports

XLSX is always generated when secrets are found. Use flags to add extra formats:

| Flag | Output |
|---|---|
| *(default)* | `<name>.xlsx` |
| `--html` | `<name>.html` — interactive, filterable, secrets hidden by default |
| `--json` | `<name>.json` — machine-readable, includes scan stats |

### HTML report

The HTML report is a self-contained single file — no dependencies, works offline. Features:

- **Search** across all columns in real time
- **Filter** by project and secret type
- **Sort** by any column
- **Secrets and context are hidden by default** — click a row's value cell to reveal both the token and its surrounding context simultaneously. Click again to hide.

### XLSX report

Each row shows: project, issue key, URL, summary, author, creation date, location, secret type, matched value, and context. When a secret is found inside an attachment, the **Location** column shows `Attachment: filename.ext`. The second sheet contains summary statistics.

---

## All flags

| Flag | Default | Description |
|---|---|---|
| `-e`, `--email` | — | Atlassian email |
| `-t`, `--token` | — | Atlassian API token |
| `-u`, `--url` | — | Jira instance URL |
| `--env` | off | Load credentials from `.env` file |
| `--env-file` | `.env` | Path to `.env` file |
| `--scan-secrets` | off | Enable secret scanning |
| `--projects` | all | Comma-separated project keys to scan |
| `--max-issues` | 0 (unlimited) | Max issues per project |
| `--patterns` | `secret_patterns.txt` | Custom patterns file (`Name:::Regex:::GroupIndex`) |
| `-tp`, `--trufflehog-patterns` | — | TruffleHog v3 YAML detectors file |
| `-tk`, `--trufflehog-keywords` | — | Include only detectors matching these keywords |
| `-tek`, `--trufflehog-exclude-keywords` | — | Exclude detectors matching these keywords |
| `--incremental` | off | Only scan issues updated since last run |
| `--state-file` | `.jira_scanner_state.json` | Path to incremental scan state file |
| `--since-days` | — | Only scan issues updated in the last N days |
| `--since-date` | — | Only scan issues updated on or after this date (`YYYY-MM-DD`) |
| `--created-after` | — | Only scan issues created on or after this date (`YYYY-MM-DD`) |
| `--ignore-file` | — | Path to false-positive whitelist file |
| `--workers` | 1 | Parallel threads for scanning (recommended: 5–20) |
| `--scan-attachments` | off | Download and scan file attachments |
| `--max-attachment-size` | unlimited | Max attachment size, e.g. `2mb`, `500kb` |
| `-o`, `--output` | auto timestamp | Output filename base (extensions added automatically) |
| `--html` | off | Generate interactive HTML report |
| `--json` | off | Generate JSON report |
| `--no-duplicates`, `-nd` | off | Deduplicate findings by `(issue_key, secret_value)` |
| `--email-sender` | — | SES sender address (must be verified) |
| `--email-recipient` | — | Recipient address(es), comma-separated |
| `--aws-region` | `eu-central-1` | AWS region for SES |
| `--notify-authors` | off | Send personal email to each issue author with their own findings |
| `--notify-domain` | — | Only notify authors on these domains, e.g. `google.com,tesla.com` |
| `-q`, `--quiet` | off | Suppress per-issue output |
| `-v`, `--verbose` | off | Verbose debug output |
