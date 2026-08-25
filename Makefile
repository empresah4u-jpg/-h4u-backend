run:
	uvicorn app.main:app --reload

db-up:
	docker compose up -d

db-down:
	docker compose down

db-status:
	docker ps

embeddings:
	python3 -m scripts.generate_embeddings

compile:
	python3 -m py_compile app/main.py
	python3 -m py_compile app/logger.py
	python3 -m py_compile app/routers/semantic_search.py

status:
	git status