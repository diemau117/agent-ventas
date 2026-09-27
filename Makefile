.PHONY: dev test migrate seed up down logs lint

# Servidor local con recarga (requiere .env y Postgres levantados).
dev:
	uvicorn app.main:app --reload --port 8000

test:
	python3 -m pytest -q

# Migraciones a head (Alembic es la fuente de verdad del schema).
migrate:
	alembic upgrade head

# Datos de demo: negocio + productos + conocimiento RAG.
seed:
	python3 -m app.db.seed_demo
	python3 -m app.rag.seed_knowledge

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f app

# No hay linter/formatter configurado en el repo; esta es la verificación
# barata que sí tenemos: compila todo app/ para cazar errores de sintaxis.
lint:
	python3 -m compileall -q app
