#!/bin/sh
echo "Content-Type: text/plain; charset=utf-8"
echo ""
pivas vless show 2>&1 | sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g'
