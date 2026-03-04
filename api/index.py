"""
Vercel serverless entry point.
"""
import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)-25s %(message)s",
)

# ── Fix import path ──
# Vercel runs this file from api/ directory
# We need the project root on sys.path so "from scraper..." works
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mangum import Mangum
from scraper.api import app

handler = Mangum(app, lifespan="off")