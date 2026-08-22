# ---- Stage 1: build the React frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python backend, serving the API + built frontend ----
FROM python:3.11-slim
WORKDIR /app

# grafana_tools.py launches the Grafana MCP server via `uvx`, so `uv` must
# be available in the runtime image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY live_titles.json .
COPY --from=frontend-build /frontend/dist ./frontend/dist

# Cloud Run injects PORT at runtime.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT}"]
