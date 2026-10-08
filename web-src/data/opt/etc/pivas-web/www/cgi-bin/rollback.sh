#!/bin/sh
[ "${REQUEST_METHOD:-GET}" = "POST" ] || {
    echo "Status: 405 Method Not Allowed"
    echo "Content-Type: text/plain; charset=utf-8"
    echo ""
    echo "POST required"
    exit 0
}
echo "Content-Type: text/plain; charset=utf-8"
echo ""
pivas vless rollback 2>&1 | sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g'
