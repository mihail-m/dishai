# Stage 1: build the React UI
FROM node:22-slim AS ui
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY public/ /app/public/
# skip the type-check here (`make build` runs it); the image only needs the bundle
RUN npx vite build

# Stage 2: FastAPI serves the API and the built UI on one port
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt
COPY backend/ ./backend/
COPY public/ ./public/
COPY data/*.npz data/*.tif ./data/
COPY --from=ui /app/frontend/dist ./frontend/dist

# Hugging Face Spaces expects 7860; Render/Cloud Run set $PORT.
ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
