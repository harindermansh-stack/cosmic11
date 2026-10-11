# Cosmic 11 web app: the page + the API, in one container.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PUBLIC_MODE=1 REPORT_DIR=/tmp/reports USAGE_FILE=/tmp/usage.json
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Download the book-search model once, at build time, so the first visitor does not wait for it.
RUN python -c "from chromadb.utils import embedding_functions as e; e.DefaultEmbeddingFunction()(['warm up'])"
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
