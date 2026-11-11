FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY sql ./sql
COPY tests ./tests
COPY data/sample ./data/sample
COPY pytest.ini .

RUN mkdir -p data/raw data/generated data/invalid reports logs

CMD ["python", "-m", "src.cli", "--help"]
