FROM python:3.11-slim

WORKDIR /app

# System deps needed by psycopg2 and chromadb's sqlite/onnx bits.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Chroma persists its index here; docker-compose mounts a volume at this path.
RUN mkdir -p /app/chroma_data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
