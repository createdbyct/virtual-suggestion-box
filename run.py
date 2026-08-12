#!/usr/bin/env python3
"""
Convenience launcher for the dev server.
Equivalent to: uvicorn app.main:app --reload --port 1994

Usage:
    python3 run.py
"""
import sys

try:
    import uvicorn
except ImportError:
    print("uvicorn isn't installed in this Python environment.")
    print("Make sure your venv is active (source venv/bin/activate), then run:")
    print("    pip install -r requirements.txt")
    sys.exit(1)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=1994, reload=True)
