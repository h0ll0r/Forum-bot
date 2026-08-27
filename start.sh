#!/bin/bash
echo "Installing Playwright dependencies..."
pip install playwright
playwright install chromium --with-deps
echo "Starting bot..."
python bot.py
