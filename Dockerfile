FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV RERANK_MODEL=cross-encoder/ms-marco-TinyBERT-L-2-v2
ENV PYTHONUNBUFFERED=1
RUN python ingest.py || echo "no PDFs at build - ingest at runtime"
EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
