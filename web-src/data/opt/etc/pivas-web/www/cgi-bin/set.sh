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
# Parse QUERY_STRING для slot=1|2
slot=$(echo "${QUERY_STRING:-}" | sed -n 's/.*slot=\([12]\).*/\1/p')
[ -z "$slot" ] && { echo "slot не задан (1 или 2)"; exit 1; }

# Parse multipart POST: вынуть field 'url'
CT=$(echo "${CONTENT_TYPE:-}" | tr -d '\r')
CL=${CONTENT_LENGTH:-0}
# tr -d '\r' обязателен — иначе awk /^$/ не матчит границу между
# multipart-headers и содержимым (там CRLF CRLF).
body=$(dd bs=1 count="$CL" 2>/dev/null | tr -d '\r')

bnd=$(echo "$CT" | sed -n 's/.*boundary=\(.*\)/\1/p')

if [ -n "$bnd" ]; then
    url=$(printf '%s' "$body" | awk -v b="--$bnd" '
        index($0, b) == 1 { inside=0; got=0; next }
        /Content-Disposition.*name="url"/ { inside=1; got=0; next }
        inside && /^$/ && !got { got=1; next }
        inside && got && NF { print; exit }
    ')
else
    # url-encoded fallback: url=vless%3A%2F%2F...
    url=$(printf '%s' "$body" | sed -n 's/^url=\(.*\)/\1/p' | \
          sed 's/%/\\x/g' | xargs -I{} printf '{}\n' 2>/dev/null || echo "")
fi

[ -z "$url" ] && { echo "url пустой"; exit 1; }
pivas vless set-"$slot" "$url" 2>&1 | sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g'
