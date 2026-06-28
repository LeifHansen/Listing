#!/usr/bin/env bash
# Convenience launcher for the eBay Listing Generator.
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

echo "Starting server on http://localhost:8000"
exec uvicorn backend.main:app --reload --host 0.0.0.0 --port "${PORT:-8000}"
