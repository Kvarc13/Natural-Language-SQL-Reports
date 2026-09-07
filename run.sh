#!/bin/sh
# Lambda Web Adapter bootstrap. Must stay executable (chmod +x).
exec python -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8080}"
