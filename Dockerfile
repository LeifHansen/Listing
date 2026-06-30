# eBay Listing Generator - container image (used by Fly.io and any Docker host)
FROM python:3.11-slim

# Pillow 11 ships manylinux wheels, so no system build deps are needed.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

EXPOSE 8080

# --proxy-headers + trusted forwarded IPs so request.base_url is the public
# https origin behind Fly's proxy (needed so eBay can fetch image URLs).
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
