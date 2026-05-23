#!/bin/bash
set -e

echo "Building RelayProtection with Nuitka..."

# Run Nuitka on the primary entry point
python -m nuitka \
    --standalone \
    --assume-yes-for-downloads \
    --include-package=api \
    --include-package=logic \
    --include-package=utils \
    --output-dir=build \
    SysEngine.py

echo "Build complete! Executable is located in build/SysEngine.dist"
echo "Make sure to copy or link the 'config' directory next to the executable before running."
