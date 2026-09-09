FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
COPY public ./public
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py database.py ai_service.py services.py product.py ./
COPY scripts ./scripts
COPY --from=frontend /app/dist ./dist
RUN useradd --create-home houseguide && mkdir -p /data/uploads /data/erasures /backups && chown -R houseguide:houseguide /app /data /backups
USER houseguide
EXPOSE 8000
CMD ["sh", "-c", "flask --app server init-db && exec gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 120 server:app"]
