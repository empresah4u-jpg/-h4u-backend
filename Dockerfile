FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV HF_HUB_DISABLE_XET=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app/embeddings.py app/embeddings.py

RUN python -c "from app.embeddings import get_model; get_model()"

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]