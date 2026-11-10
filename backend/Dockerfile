FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/presscheck ./backend/presscheck
COPY dataset ./dataset
ENV PYTHONPATH=/app/backend PRESSCHECK_DATA=/data
RUN useradd -m -u 10001 inspector && mkdir /data && chown inspector /data
USER inspector
CMD ["uvicorn","presscheck.api:app","--host","0.0.0.0","--port","8000","--workers","1"]
