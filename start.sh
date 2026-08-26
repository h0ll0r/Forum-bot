#!/bin/bash
echo "Installing Playwright browsers..."
playwright install chromium
playwright install-deps chromium
echo "Starting bot..."
python bot.py
