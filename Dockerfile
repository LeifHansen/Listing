# Thryft Shop - container image (used by Fly.io and any Docker host)

# Stage 1: build the React frontend (Vite outputs static files to dist/).
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

# Stage 2: the Python backend serves the API + the built frontend.
FROM python:3.11-slim

# Pillow 11 ships manylinux wheels, so no system build deps are needed.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the background-removal models into the image so there's no slow/fragile
# download at request time. u2netp (~4MB) is the safe fallback that runs even
# on a 2GB machine; isnet-general-use (~176MB) is the quality model production
# selects via REMBG_MODEL in fly.toml (needs the 4GB VM).
RUN python -c "from rembg import new_session; new_session('u2netp'); new_session('isnet-general-use')"

COPY backend ./backend
COPY --from=frontend /app/frontend/dist ./frontend/dist

EXPOSE 8080

# --proxy-headers + trusted forwarded IPs so request.base_url is the public
# https origin behind Fly's proxy (needed so eBay can fetch image URLs).
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
