# Dockerfile — Agent Ventas
FROM python:3.12-slim

WORKDIR /app

# Instalar certificados CA para SSL con Render PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY app/ app/
COPY frontend/ frontend/
COPY alembic.ini .
COPY alembic/ alembic/

# Variables de entorno
ENV PYTHONUNBUFFERED=1
ENV APP_ENV=prod

# Migraciones + start
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT"]
