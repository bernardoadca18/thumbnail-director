FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libvips42 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
ENV VIPS_CONCURRENCY=2
WORKDIR /service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY config config
RUN useradd --uid 10001 --create-home director && mkdir -p /data/thumbnails && chown director /data/thumbnails
USER director
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
