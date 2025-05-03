#!/bin/bash

echo "📡 ADB Setup + Connect Script"

# Detect package manager
if command -v apt &>/dev/null; then
    PM="sudo apt"
elif command -v pkg &>/dev/null; then
    PM="pkg"
elif command -v gsudo &>/dev/null; then
    PM="gsudo apt"
else
    echo "❌ No supported package manager found (apt/pkg/gsudo)."
    exit 1
fi

# Install ADB
if ! command -v adb &>/dev/null; then
    echo "📦 Installing ADB..."
    $PM update -y
    $PM install android-tools-adb -y
else
    echo "✅ ADB already installed"
fi

# Ask for Bliss OS IP
read -p "📥 Enter Bliss OS IP (e.g. 192.168.x.x): " BLISS_IP

if [[ -z "$BLISS_IP" ]]; then
    echo "❌ IP cannot be empty."
    exit 1
fi

echo "🔌 Connecting to $BLISS_IP:5555..."
adb connect "$BLISS_IP:5555"

echo "📋 Current ADB devices:"
adb devices
