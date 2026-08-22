# H4U API

Backend inicial de H4U desarrollado con FastAPI y PostgreSQL.

## Tecnologías

- FastAPI
- PostgreSQL
- pgvector
- Docker
- Sentence Transformers
- Swagger / OpenAPI

## Endpoints principales

- GET /health
- GET /destinations
- GET /hotels
- GET /restaurants
- GET /tours
- GET /attractions
- GET /transport/routes
- GET /search
- GET /semantic-search

## Configuración

1. Crear entorno virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
