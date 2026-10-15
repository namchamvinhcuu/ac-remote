# Pinned to the image the app was built and tested on (Python 3.12.14, Debian trixie).
FROM python:3.12.14-slim-trixie@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt

COPY app ./app
RUN useradd --system --uid 10001 appuser && mkdir -p /app/data /app/logs && chown appuser /app/data /app/logs
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "app.main:build_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
