#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
exec python3 -m jarvis "$@"
