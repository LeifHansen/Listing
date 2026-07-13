#!/usr/bin/env bash
# Convenience launcher for QuickFlip.
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
  echo "No .env found; copying .env.example -> .env (add your ANTHROPIC_API_KEY)."
  cp .env.example .env
fi

# Build the React frontend (the backend serves frontend/dist). For frontend
# development with hot reload, run `npm run dev` in frontend/ instead — it
# proxies /api and /media to this server.
if command -v npm >/dev/null 2>&1; then
  echo "Building frontend..."
  (cd frontend && npm install --no-audit --no-fund && npm run build)
elif [ ! -d "frontend/dist" ]; then
  echo "ERROR: Node.js/npm is required to build the frontend (frontend/dist is missing)."
  echo "Install Node 20+ and re-run, or copy a prebuilt frontend/dist here."
  exit 1
fi

echo "Starting server on http://localhost:8000"
exec uvicorn backend.main:app --reload --host 0.0.0.0 --port "${PORT:-8000}"
