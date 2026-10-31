#!/bin/bash
cd "$(dirname "$0")"
echo "Starting Stack Converter on http://$(hostname -f):8765 ..."
exec python3 server.py
