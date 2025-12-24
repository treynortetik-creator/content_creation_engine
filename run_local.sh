#!/bin/bash
# ContentMultiplier - Local Development Script for Mac
# Usage: ./run_local.sh

set -e

echo "🚀 ContentMultiplier Local Development Setup"
echo "============================================="

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed."
    echo "   Install with: brew install python3"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "✅ Found Python $PYTHON_VERSION"

# Check for pip
if ! command -v pip3 &> /dev/null; then
    echo "❌ pip3 is required but not installed."
    exit 1
fi
echo "✅ Found pip3"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
fi

# Activate virtual environment
echo "🔄 Activating virtual environment..."
source venv/bin/activate

# Install/upgrade dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt --quiet

# Check for .env file
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo "⚠️  No .env file found. Creating from .env.example..."
        cp .env.example .env
        echo "📝 Please edit .env and add your API keys:"
        echo "   - OPENROUTER_API_KEY or ANTHROPIC_API_KEY (required for content generation)"
        echo "   - GEMINI_API_KEY (required for video/audio transcription)"
        echo "   - SECRET_KEY (for JWT authentication)"
        echo ""
        echo "   Then run this script again."
        exit 0
    else
        echo "❌ No .env or .env.example file found!"
        exit 1
    fi
fi

# Check for required API keys
source .env 2>/dev/null || true

if [ -z "$OPENROUTER_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️  Warning: No OPENROUTER_API_KEY or ANTHROPIC_API_KEY found in .env"
    echo "   Content generation features will not work without one of these keys."
fi

if [ -z "$GEMINI_API_KEY" ]; then
    echo "⚠️  Warning: No GEMINI_API_KEY found in .env"
    echo "   Video and audio transcription will not work without this key."
    echo "   Text files (.txt, .md) can still be processed."
fi

# Create required directories
echo "📁 Creating required directories..."
mkdir -p database uploads data/prompts clients

# Start the server
PORT=${PORT:-5000}
echo ""
echo "============================================="
echo "🌐 Starting ContentMultiplier server..."
echo "   Local URL: http://localhost:$PORT"
echo "   Press Ctrl+C to stop"
echo "============================================="
echo ""

python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --reload
