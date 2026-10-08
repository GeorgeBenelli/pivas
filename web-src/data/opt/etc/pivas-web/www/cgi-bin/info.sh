#!/bin/sh
# /cgi-bin/info.sh — status, mem, disk, slot1/2 config (short)
echo "Content-Type: text/plain; charset=utf-8"
echo ""

# hostname + uptime
up=$(awk '{print int($1)}' /proc/uptime)
d=$((up/86400)); h=$(((up%86400)/3600)); m=$(((up%3600)/60))
[ $d -gt 0 ] && UP="${d} дн. ${h} ч." || UP="${h} ч. ${m} мин."

# memory
mt=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
mf=$(awk '/MemAvailable/ {print int($2/1024); exit} /MemFree/{ma=$2/1024}END{print int(ma)}' /proc/meminfo | head -1)
mu=$((mt - mf))

# load
load=$(awk '{print $1}' /proc/loadavg)

# xray процессы
xc=$(pidof xray 2>/dev/null | wc -w)

# slot addresses via jq (если есть)
slot1="?" ; slot2="?"
if [ -f /opt/etc/xray/pivas.json ] && command -v jq >/dev/null; then
    slot1=$(jq -r '.outbounds[]|select(.tag=="slot-1")|.settings.vnext[0].address // "?"' /opt/etc/xray/pivas.json 2>/dev/null)
    slot2=$(jq -r '.outbounds[]|select(.tag=="slot-2")|.settings.vnext[0].address // "?"' /opt/etc/xray/pivas.json 2>/dev/null)
fi

# pivas list size
kvn=$(grep -cv '^#\|^$' /opt/etc/pivas.list 2>/dev/null)

cat <<EOF
Uptime:       $UP
Память:       $mu / $mt МБ (свободно $mf)
Нагрузка CPU: $load
xray:         $([ $xc -gt 0 ] && echo "работает ($xc проц.)" || echo "остановлен")
VPN slot-1:   $slot1
VPN slot-2:   $slot2
Доменов:      $kvn
EOF
