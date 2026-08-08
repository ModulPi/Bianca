FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY agent ./agent

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
