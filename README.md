# jira-scanner

Scans all Jira issues (title, description, comments) across all or selected projects. Detects 40+ secret types: AWS keys, GitHub tokens, Stripe keys, OpenAI keys, database connection strings, and more. Exports findings to a formatted .xlsx report. Supports email delivery of reports via AWS SES.

#### Configuration
1. Create your .env file
Copy the example and fill in your credentials:
```
cp .env.example .env
```
2. Get a Jira API Token
```
Log in to id.atlassian.com
Go to Security → API tokens
Click Create API token, give it a name
Copy the token value — it is shown only once
```
🐳 Running with Docker

Build the image
```
docker build -t jira-secret-scanner .
```
Run with plain Docker
```
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets --output /reports/jira_secrets_report.xlsx
```
Run with email notification
```
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  -e AWS_ACCESS_KEY_ID=AKIA... \
  -e AWS_SECRET_ACCESS_KEY=... \
  jira-secret-scanner \
  --env --scan-secrets \
  --output /reports/jira_secrets_report.xlsx \
  --email-sender scanner@company.com \
  --email-recipient security@company.com \
  --aws-region eu-central-1
```
