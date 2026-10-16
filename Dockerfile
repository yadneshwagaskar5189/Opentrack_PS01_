# One container: builds the website, then runs FastAPI, which serves the API (/api) and the website (/).
# Used by render.yaml (Render.com). Works the same on Railway, Fly.io or any Docker host.

# ---- 1. build the React frontend
FROM node:20-slim AS website
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. the backend
FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY seed_data ./seed_data
COPY --from=website /web/dist ./frontend/dist
EXPOSE 8000
# The database is built from seed_data/*.csv on first start. Secrets (LLM_API_KEY...) come from the host's settings.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
