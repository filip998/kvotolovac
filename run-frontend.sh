#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/frontend"

# Install deps if needed
if [ ! -d "node_modules" ]; then
    echo "📦 Installing dependencies..."
    npm install
fi

# Run dev server
echo "🚀 Starting frontend on http://localhost:5173"
npm run dev
