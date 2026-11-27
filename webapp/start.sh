#!/bin/bash
# ローカルWebアプリを起動する。http://127.0.0.1:8795/ （この Mac からだけ開ける）
cd "$(dirname "$0")/.." || exit 1
mkdir -p webapp/logs
exec .venv/bin/python webapp/server.py "${1:-8795}"
