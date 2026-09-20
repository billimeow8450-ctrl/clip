#!/usr/bin/env bash
set -e

echo "======================================================="
echo "   🚀 Starting ClipStudio AI SaaS (Full-Stack)        "
echo "======================================================="

# Navigate to project root
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Ensure storage directories exist
mkdir -p backend/storage/uploads backend/storage/outputs backend/data

# Laptop-safe mode: by default ENABLE_HEAVY_RENDERING is 0 to avoid cooking the laptop.
# On your production server, set: export ENABLE_HEAVY_RENDERING=1
export ENABLE_HEAVY_RENDERING="${ENABLE_HEAVY_RENDERING:-0}"

echo ">> Safe Laptop Mode: ENABLE_HEAVY_RENDERING=$ENABLE_HEAVY_RENDERING"

# Detect virtualenv python if available
PYTHON_BIN="python3"
if [ -f ".venv/bin/python3" ]; then
    PYTHON_BIN=".venv/bin/python3"
fi

# Start FastAPI Backend in background
echo ">> Starting FastAPI Backend on http://localhost:8000..."
$PYTHON_BIN -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Trap signals to cleanup on exit
trap "echo 'Stopping services...'; kill $BACKEND_PID 2>/dev/null || true; exit 0" SIGINT SIGTERM EXIT

# Start Frontend Dev Server
echo ">> Starting React + Vite Frontend on http://localhost:5173..."
cd frontend
npm run dev

# Wait for background processes
wait $BACKEND_PID
