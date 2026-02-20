FROM python:3.11-slim

WORKDIR /app

# Устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем скрипт и файл с паттернами
COPY jira_scanner.py .
COPY regex.txt .

# Директория для отчётов (монтируется снаружи)
RUN mkdir /reports

ENTRYPOINT ["python", "jira_scanner.py"]
