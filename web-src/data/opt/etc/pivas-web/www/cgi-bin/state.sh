#!/bin/sh
# /cgi-bin/state.sh — единый JSON endpoint для UI.
# Один вызов: вся инфа о pivas (xray-status, memory, slot1/2 url+server+domains).
# Не дергает `pivas vless ...` (каждый вызов ~2 с загрузки библиотек): читает
# pivas.json общим экспортёром и списки напрямую. Результат кэшируется на CACHE_TTL секунд,
# чтобы несколько вкладок/частый опрос не держали CPU роутера.
echo "Content-Type: application/json; charset=utf-8"
echo "Cache-Control: no-store, no-cache, must-revalidate"
echo "Pragma: no-cache"
echo ""

CACHE=/opt/tmp/pivas-web-state.json
CACHE_TTL=8
XRAY_CFG=/opt/etc/xray/pivas.json
LIST1=/opt/etc/pivas.list
LIST2=/opt/etc/pivas-slot2.list

# ---- кэш: свежий и новее исходных файлов ----
if [ -s "$CACHE" ]; then
    now=$(date +%s)
    age=$(( now - $(date -r "$CACHE" +%s 2>/dev/null || echo 0) ))
    if [ "$age" -ge 0 ] && [ "$age" -lt "$CACHE_TTL" ] \
        && [ ! "$XRAY_CFG" -nt "$CACHE" ] && [ ! "$LIST1" -nt "$CACHE" ] && [ ! "$LIST2" -nt "$CACHE" ] && [ ! /opt/etc/pivas.paused -nt "$CACHE" ] && [ ! /opt/etc/pivas.conf -nt "$CACHE" ]; then
        cat "$CACHE"
        exit 0
    fi
fi

# ---- системное ----
up=$(awk '{print int($1)}' /proc/uptime)
d=$((up/86400)); h=$(((up%86400)/3600)); m=$(((up%3600)/60))
if [ $d -gt 0 ]; then UP="${d} дн. ${h} ч."; else UP="${h} ч. ${m} мин."; fi

mt=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
mf=$(awk '/MemAvailable/ {print int($2/1024); exit}' /proc/meminfo)
[ -z "$mf" ] && mf=$(awk '/MemFree/ {print int($2/1024); exit}' /proc/meminfo)
mu=$((mt - mf))

if pidof xray >/dev/null 2>&1; then xr="true"; else xr="false"; fi

paused=false; [ -f /opt/etc/pivas.paused ] && paused=true
vpn_enabled=true; [ "$paused" = true ] && vpn_enabled=false
route_ready=false
ip route show table 1001 2>/dev/null | grep -Eq '^default dev (t2s21|tsbr21)( |$)' && route_ready=true
# ---- слоты: общий с CLI/ботом экспорт, безопасные shell-кавычки ----
slot1_srv=""; slot1_url=""; slot1_transport=""
slot2_srv=""; slot2_url=""; slot2_transport=""; slot2_same=false
if [ -f "$XRAY_CFG" ]; then
    eval "$(/opt/bin/python3 /opt/apps/pivas/bin/main/vless_url.py state-shell "$XRAY_CFG" 2>/dev/null)"
fi

# ---- домены из файлов списков (без комментариев, нижний регистр) ----
list_doms() {
    [ -f "$1" ] || return 0
    sed -E 's/#.*//; s/[[:space:]]//g; s/^\*?\.//' "$1" \
        | grep -E '^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z]+$' \
        | tr '[:upper:]' '[:lower:]' | sort -u
}
dom2=$(list_doms "$LIST2")
if [ -n "$dom2" ]; then
    _s2=$(mktemp 2>/dev/null || echo "/opt/tmp/pivas-web-s2.$$")
    printf '%s\n' "$dom2" > "$_s2"
    dom1=$(list_doms "$LIST1" | grep -vxFf "$_s2")
    rm -f "$_s2"
else
    dom1=$(list_doms "$LIST1")
fi

# ---- JSON ----
json_array() {
    awk 'BEGIN{printf "["} NF{ if (n++) printf ","; gsub(/\\/,"\\\\"); gsub(/"/,"\\\""); printf "\"%s\"",$0} END{printf "]"}'
}
json_str() {
    printf '%s' "$1" | awk '{gsub(/\\/,"\\\\"); gsub(/"/,"\\\""); printf "%s",$0}'
}

TMP="${CACHE}.$$"
{
printf '{'
printf '"xray":%s,"paused":%s,"vpn_enabled":%s,"route_ready":%s,' "$xr" "$paused" "$vpn_enabled" "$route_ready"
printf '"uptime":"%s",' "$(json_str "$UP")"
printf '"mem_used":%s,"mem_total":%s,' "$mu" "$mt"
printf '"slot1":{"server":"%s","url":"%s","transport":"%s","domains":' "$(json_str "$slot1_srv")" "$(json_str "$slot1_url")" "$(json_str "$slot1_transport")"
printf '%s' "$dom1" | json_array
printf '},"slot2":{"server":"%s","url":"%s","transport":"%s","same_as_slot1":%s,"domains":' "$(json_str "$slot2_srv")" "$(json_str "$slot2_url")" "$(json_str "$slot2_transport")" "$slot2_same"
printf '%s' "$dom2" | json_array
printf '}}'
} > "$TMP" 2>/dev/null
mv -f "$TMP" "$CACHE" 2>/dev/null
cat "$CACHE"
