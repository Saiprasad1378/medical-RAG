FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
ENV RERANK_MODEL=cross-encoder/ms-marco-TinyBERT-L-2-v2
# Render free needs PORT env, and less memory -> single worker, no reload
EXPOSE 10000
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
