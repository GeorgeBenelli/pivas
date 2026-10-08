#!/bin/sh
# /cgi-bin/ping.sh?slot=1|2|both
# Двойной пинг каждого VLESS-слота:
#   net   — чистый TCP-connect до host:port VLESS-сервера (как Happ на iPhone)
#   e2e   — HTTPS через xray-прокси до cp.cloudflare.com/generate_204
#           (проверка что трафик реально ходит через Reality)
#
# JSON: {slot1:{ok,ip,net_ms,e2e_ms,err}, slot2:{...}}

echo "Content-Type: application/json; charset=utf-8"
echo "Cache-Control: no-store"
echo ""

# Быстрый 204-эндпоинт. 200 байт ответа, закрывается моментально.
TARGET="https://cp.cloudflare.com/generate_204"
CONNECT_TIMEOUT=4
TOTAL_TIMEOUT=6

slot_q=$(echo "${QUERY_STRING:-}" | sed -n 's/.*slot=\([12]\|both\).*/\1/p')
[ -z "$slot_q" ] && slot_q="both"

# Берём host:port из pivas.json (jq уже нужен для state.sh).
get_hostport() {
    [ -f /opt/etc/xray/pivas.json ] || { echo ""; return; }
    jq -r --arg tag "slot-$1" '
        .outbounds[] | select(.tag==$tag)
        | (.settings.vnext[0].address // "") + ":" + (.settings.vnext[0].port|tostring)
    ' /opt/etc/xray/pivas.json 2>/dev/null
}

# net: чистый TCP-connect до VLESS-сервера (без xray, без TLS).
# curl --connect-timeout + -w '%{time_connect}' даёт время только TCP-connect.
# Мы не довершим TLS (сервер с Reality нам fake-сертификат), но time_connect
# уже известно к этому моменту — это именно то, что меряет Happ.
net_ms() {
    hostport=$1
    [ -z "$hostport" ] || [ "$hostport" = ":" ] && { echo "-1"; return; }
    host=${hostport%:*}
    port=${hostport##*:}
    t=$(curl -s -k -o /dev/null \
        --connect-timeout "$CONNECT_TIMEOUT" --max-time "$CONNECT_TIMEOUT" \
        -w '%{time_connect}' "https://${host}:${port}/" 2>/dev/null)
    # t = "0.178" либо "0.000" при таймауте
    awk -v t="$t" 'BEGIN{if(t+0==0){print -1} else {printf "%d", t*1000}}'
}

# e2e: полный HTTPS через xray (проверка работоспособности).
e2e_probe() {
    proxy=$1
    out=$(curl -s -x "$proxy" --max-time "$TOTAL_TIMEOUT" \
          -w '\n__meta__ %{http_code} %{time_total}\n' \
          "$TARGET" 2>/dev/null)
    rc=$?
    if [ $rc -ne 0 ]; then
        case $rc in
            28) echo "err:timeout"; return ;;
            7)  echo "err:no-proxy"; return ;;
            *)  echo "err:rc=$rc"; return ;;
        esac
    fi
    code=$(printf '%s\n' "$out" | awk '/^__meta__/ {print $2; exit}')
    ttot=$(printf '%s\n' "$out" | awk '/^__meta__/ {print $3; exit}')
    ms=$(awk -v t="$ttot" 'BEGIN{printf "%d", t*1000}')
    if [ "$code" = "204" ] || [ "$code" = "200" ]; then
        echo "ok:$ms"
    else
        echo "err:http=$code"
    fi
}

# Для exit-IP делаем отдельный маленький запрос (тот же прокси).
exit_ip() {
    proxy=$1
    curl -s -x "$proxy" --max-time "$TOTAL_TIMEOUT" \
         https://ifconfig.me 2>/dev/null | tr -d '\r\n' | head -c 64
}

probe_slot() {
    slot=$1
    proxy=$2
    hostport=$(get_hostport "$slot")

    nm=$(net_ms "$hostport")
    res=$(e2e_probe "$proxy")

    case "$res" in
        ok:*)
            e2e_val=${res#ok:}
            ip=$(exit_ip "$proxy")
            printf '{"ok":true,"ip":"%s","net_ms":%s,"e2e_ms":%s,"host":"%s"}' \
                "$ip" "$nm" "$e2e_val" "$hostport"
            ;;
        err:*)
            err=${res#err:}
            printf '{"ok":false,"err":"%s","net_ms":%s,"host":"%s"}' \
                "$err" "$nm" "$hostport"
            ;;
    esac
}

printf '{'
if [ "$slot_q" = "1" ] || [ "$slot_q" = "both" ]; then
    printf '"slot1":'
    probe_slot 1 "socks5h://127.0.0.1:1097"
    [ "$slot_q" = "both" ] && printf ','
fi
if [ "$slot_q" = "2" ] || [ "$slot_q" = "both" ]; then
    printf '"slot2":'
    probe_slot 2 "http://127.0.0.1:1098"
fi
printf '}'
