#!/usr/bin/env python3
"""
GAMDL Web App Launcher
Cross-platform startup script for the GAMDL web interface
"""

import os
import sys
import subprocess
import platform
from pathlib import Path

def check_virtual_env():
    """Check if we're in a virtual environment"""
    return hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)

def setup_virtual_env():
    """Set up virtual environment if it doesn't exist"""
    if not Path('venv').exists():
        print("❌ Virtual environment not found!")
        print("Creating virtual environment...")
        subprocess.run([sys.executable, '-m', 'venv', 'venv'], check=True)

    # Activate virtual environment
    if platform.system() == 'Windows':
        activate_script = Path('venv/Scripts/activate.bat')
        python_exe = Path('venv/Scripts/python.exe')
    else:
        activate_script = Path('venv/bin/activate')
        python_exe = Path('venv/bin/python')

    if not python_exe.exists():
        print("❌ Virtual environment activation failed!")
        return False

    return str(python_exe)

def install_dependencies(python_exe):
    """Install required dependencies"""
    try:
        # Check if dependencies are already installed
        subprocess.run([python_exe, '-c', 'import flask, werkzeug, requests'],
                      check=True, capture_output=True)
        print("✅ Dependencies already installed")
        return True
    except subprocess.CalledProcessError:
        print("📦 Installing dependencies...")
        try:
            subprocess.run([python_exe, '-m', 'pip', 'install', '-r', 'web_requirements.txt'],
                          check=True)
            print("✅ Dependencies installed successfully")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to install dependencies: {e}")
            return False

def check_cookies():
    """Check if cookies.txt exists"""
    if Path('cookies.txt').exists():
        print("✅ Cookies file found")
        return True
    else:
        print("⚠️  Warning: cookies.txt not found")
        print("   You'll need to upload cookies through the web interface")
        return False

def create_directories():
    """Create necessary directories"""
    Path('uploads').mkdir(exist_ok=True)
    Path('downloads').mkdir(exist_ok=True)
    print("✅ Directories ready")

def get_network_ip():
    """Get the network IP address"""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "localhost"

def main():
    print("🎵 GAMDL Web App Launcher")
    print("=" * 30)

    # Check if we're in virtual environment
    if not check_virtual_env():
        print("🔄 Setting up virtual environment...")
        python_exe = setup_virtual_env()
        if not python_exe:
            return 1
    else:
        python_exe = sys.executable
        print("✅ Already in virtual environment")

    # Install dependencies
    if not install_dependencies(python_exe):
        return 1

    # Check cookies
    check_cookies()

    # Create directories
    create_directories()

    # Get network IP
    network_ip = get_network_ip()

    # Start the Flask app
    print("\n🚀 Starting GAMDL Web App...")
    print("\n🌐 Web interface will be available at:")
    print(f"   Local:   http://127.0.0.1:5000")
    print(f"   Network: http://localhost:5000")
    if network_ip != "localhost":
        print(f"   Network: http://{network_ip}:5000")
    print("\nPress Ctrl+C to stop the server\n")

    try:
        # Run the Flask app
        subprocess.run([python_exe, 'web_app.py'], check=True)
    except KeyboardInterrupt:
        print("\n👋 Server stopped")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to start server: {e}")
        return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())