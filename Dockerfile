# Thryft Shop - container image (used by Fly.io and any Docker host)

# Stage 1: build the React frontend (Vite outputs static files to dist/).
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
# Vite bakes VITE_* in at build time. A crash reported from the browser sends
# a MINIFIED stack (no source maps are emitted), so the only way to resolve it
# later is to know which bundle it came from. Same sha stage 2 stamps on
# /api/health, so the two agree about what is running.
ARG GIT_SHA=""
ENV VITE_BUILD_SHA=$GIT_SHA
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

# Which commit this image was built from. Deploys report success on the
# workflow side while the running code is something else -- a poisoned builder
# cache did exactly that here before (see the builder-destroy in git history) --
# and there was no way to ask the app what it was running. /api/health reports
# this, so "is production current?" is one field instead of an investigation.
# Last, so it never invalidates the model bake above.
ARG GIT_SHA=""
ENV BUILD_SHA=$GIT_SHA

COPY backend ./backend
COPY --from=frontend /app/frontend/dist ./frontend/dist
# The revision set travels with the code that expects it, so the cutover from
# create_all is `alembic stamp head` then `alembic upgrade head` run against
# the machine, rather than a redeploy carrying a script that is not there.
# Nothing on the boot path reads these; see alembic/env.py.
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

EXPOSE 8080

# --proxy-headers + trusted forwarded IPs so request.base_url is the public
# https origin behind Fly's proxy (needed so eBay can fetch image URLs).
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
