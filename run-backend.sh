#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/backend"

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install deps
echo "📦 Installing dependencies..."
pip install -q -r requirements.txt

# Run server
echo "🚀 Starting backend on http://localhost:8000"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
