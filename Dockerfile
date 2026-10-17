FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATABASE_URL=sqlite:////tmp/adsentry.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY web ./web

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Demo tenant switching for the bundled dashboard. Set ADSENTRY_DEMO=0 behind
# a real auth proxy.
ENV ADSENTRY_DEMO=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/healthz', timeout=2)"

CMD ["sh", "-c", "uvicorn adsentry.main:app --app-dir src --host 0.0.0.0 --port ${PORT:-8080}"]
