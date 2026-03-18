# 🐳 Jira Secrets Scanner

Scans Jira issues for exposed secrets - API keys, tokens, passwords, and credentials. Detects 50+ secret types out of the box, with optional TruffleHog patterns to expand coverage to 1500+. Exports findings across all or selected projects to a `.xlsx` report.

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

All commands mount two volumes:
- `./reports` — output directory where the `.xlsx` report is saved
- `./.env` — credentials file (read-only)

### Scan all projects

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets --output /reports/jira_secrets.xlsx
```

### Scan all projects (JSON output format)

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets --json --output /reports/jira_secrets.json
```

### Scan all projects with trufflehog patterns (1600+ patterns)

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  -v $(pwd)/trufflehog.yaml:/app/trufflehog.yaml:ro \
  jira-secret-scanner \
  --trufflehog-patterns /app/trufflehog.yaml \
  --env --scan-secrets --output /reports/jira_secrets.xlsx
```

### Scan specific projects

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --projects PROJ1,PROJ2 \
  --output /reports/jira_secrets.xlsx
```

### Limit issues per project

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --max-issues 100 \
  --output /reports/jira_secrets.xlsx
```

### Scan attachments

By default, only text fields are scanned. To also scan attached files, add `--scan-attachments`:

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --scan-attachments \
  --output /reports/jira_secrets.xlsx
```

To skip large files, set a size limit:

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets \
  --scan-attachments --max-attachment-size 5mb \
  --output /reports/jira_secrets.xlsx
```

### Send report by email

Requires [AWS SES](https://aws.amazon.com/ses/) with a verified sender address.

```bash
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  -e AWS_ACCESS_KEY_ID=AKIA... \
  -e AWS_SECRET_ACCESS_KEY=... \
  jira-secret-scanner \
  --env --scan-secrets \
  --output /reports/jira_secrets.xlsx \
  --email-sender scanner@company.com \
  --email-recipient security@company.com \
  --aws-region eu-west-1
```

---

## What gets scanned?

### By default (`--scan-secrets`)

The scanner reads the following text fields from every issue:

| Field | Description |
|---|---|
| **Summary** | Issue title |
| **Description** | Issue body (plain text and Atlassian Document Format) |
| **Comments** | All comments on the issue |

No files are downloaded. Fast and sufficient for secrets pasted directly into Jira.

### With `--scan-attachments`

Each attachment is downloaded and its text is extracted before scanning. Supported file types:

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

| Format | Extensions | Method |
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

## Report

The `.xlsx` report is saved to `./reports/` on the host. Each row shows the project, issue, secret type, matched value, and context. When a secret is found inside an attachment, the **Location** column shows `Attachment: filename.ext`.
