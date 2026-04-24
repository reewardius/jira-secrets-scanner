FROM python:3.11-slim

WORKDIR /app

# System dependencies: Tesseract OCR + language packs + PDF libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-rus \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy scanner
COPY jira_scanner.py .
COPY regex.txt secret_patterns.txt
COPY trufflehog.yaml .

# Output directory (mount from host)
RUN mkdir /reports

ENTRYPOINT ["python", "jira_scanner.py"]
