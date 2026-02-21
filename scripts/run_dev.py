"""Development server runner.

Usage: python scripts/run_dev.py
"""

import sys
import os

# Ensure src/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import uvicorn

uvicorn.run(
    "patt.app:create_app",
    host="127.0.0.1",
    port=8100,
    reload=True,
    factory=True,
    reload_dirs=["src"],
)
