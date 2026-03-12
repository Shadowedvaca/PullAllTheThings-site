FROM python:3.11-slim

WORKDIR /app

# System deps for asyncpg (needs libpq) and bcrypt
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY data/ data/
COPY alembic/ alembic/
COPY alembic.ini .

# Run migrations then start app
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

ENV PYTHONPATH=/app/src

EXPOSE 8100

ENTRYPOINT ["./docker-entrypoint.sh"]
