# jira-scanner
docker build
```
# Сборка
docker build -t jira-secret-scanner .

# Запуск
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  jira-secret-scanner \
  --env --scan-secrets --output /reports/report.xlsx
```
scan ses
```
docker run --rm \
  -v $(pwd)/reports:/reports \
  -v $(pwd)/.env:/app/.env:ro \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  jira-secret-scanner \
  --env --scan-secrets \
  --email-sender sender@company.com \
  --email-recipient security@company.com \
  --aws-region eu-west-1
```
