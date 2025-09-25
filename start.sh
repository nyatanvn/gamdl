#!/bin/bash

# GAMDL Web App Startup Script
# This script sets up and runs the GAMDL web interface

set -e  # Exit on any error

echo "🎵 GAMDL Web App Startup Script"
echo "================================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found!"
    echo "Please run: python3 -m venv venv && source venv/bin/activate && pip install -r web_requirements.txt"
    exit 1
fi

echo "✅ Virtual environment found"

# Activate virtual environment
echo "🔄 Activating virtual environment..."
source venv/bin/activate

# Check if dependencies are installed
echo "🔍 Checking dependencies..."
python -c "import flask, werkzeug, requests" 2>/dev/null || {
    echo "📦 Installing dependencies..."
    pip install -r web_requirements.txt
}

echo "✅ Dependencies ready"

# Check if cookies.txt exists
if [ ! -f "cookies.txt" ]; then
    echo "⚠️  Warning: cookies.txt not found"
    echo "   You'll need to upload cookies through the web interface"
else
    echo "✅ Cookies file found"
fi

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p uploads downloads

# Start the Flask app
echo "🚀 Starting GAMDL Web App..."
echo ""
echo "🌐 Web interface will be available at:"
echo "   Local:   http://127.0.0.1:5000"
echo "   Network: http://$(hostname -I | awk '{print $1}'):5000"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

python web_app.py