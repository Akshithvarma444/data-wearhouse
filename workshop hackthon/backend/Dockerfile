# Backend image. Build context = the repository root ("workshop hackthon/"),
# because the API also needs other/sql:
#   docker build -f backend/Dockerfile -t sales-dw-backend .
# (other/docker/docker-compose.yml does this for you.)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SQL_DIR=/srv/other/sql \
    DATASETS_DIR=/srv/other/datasets \
    REPORTS_DIR=/srv/other/reports \
    LOGS_DIR=/srv/backend/logs

WORKDIR /srv/backend

# Optional extra root CAs (antivirus HTTPS scanning / corporate proxy).
# See other/docker/certs/README.md. With an empty folder this is a no-op.
COPY other/docker/certs/ /tmp/extra-certs/
RUN cat /etc/ssl/certs/ca-certificates.crt /tmp/extra-certs/*.crt > /tmp/ca-bundle.pem 2>/dev/null \
    || cp /etc/ssl/certs/ca-certificates.crt /tmp/ca-bundle.pem

COPY backend/requirements.txt .
RUN PIP_CERT=/tmp/ca-bundle.pem pip install -r requirements.txt

COPY backend/app ./app
COPY backend/tests ./tests
COPY backend/pytest.ini .
COPY other/sql /srv/other/sql
COPY other/datasets/sample /srv/other/datasets/sample
COPY other/datasets/invalid /srv/other/datasets/invalid

RUN useradd --create-home appuser \
    && mkdir -p /srv/other/datasets/generated /srv/other/datasets/uploads /srv/other/reports /srv/backend/logs \
    && chown -R appuser /srv
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
