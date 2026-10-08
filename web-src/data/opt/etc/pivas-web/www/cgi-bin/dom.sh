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
slot=$(echo "${QUERY_STRING:-}" | sed -n 's/.*slot=\([12]\).*/\1/p')
act=$(echo "${QUERY_STRING:-}"  | sed -n 's/.*action=\(add\|del\).*/\1/p')
[ -z "$slot" ] || [ -z "$act" ] && { echo "нужен slot=1|2 и action=add|del"; exit 1; }

CT=$(echo "${CONTENT_TYPE:-}" | tr -d '\r')
CL=${CONTENT_LENGTH:-0}
# Читаем body И СРАЗУ убираем \r — иначе awk /^$/ не матчит границу
# между multipart-headers и содержимым (там CRLF CRLF, awk видит строку
# из одного "\r" вместо пустой).
body=$(dd bs=1 count="$CL" 2>/dev/null | tr -d '\r')
bnd=$(echo "$CT" | sed -n 's/.*boundary=\(.*\)/\1/p')

if [ -n "$bnd" ]; then
    domains=$(printf '%s' "$body" | awk -v b="--$bnd" '
        index($0, b) == 1 { inside=0; got=0; next }
        /Content-Disposition.*name="domains"/ { inside=1; got=0; next }
        inside && /^$/ && !got { got=1; next }
        inside && got && NF { print }
    ')
else
    # application/x-www-form-urlencoded fallback
    domains=$(printf '%s' "$body" \
        | sed -n 's/^domains=//p; s/&domains=/\n/gp' \
        | sed 's/%20/ /g; s/%2B/ /g; s/+/ /g' \
        | head -200)
fi
[ -z "$(echo "$domains" | tr -d ' \t\n')" ] && { echo "доменов нет"; exit 1; }

# Нормализация массового ввода: комментарии, запятые/точки с запятой как
# разделители, схема/путь/www./ведущая точка, нижний регистр, дедуп.
domains=$(printf '%s\n' "$domains" \
    | sed -E 's/#.*$//; s/[,;]+/ /g' \
    | tr ' \t' '\n\n' \
    | sed -E '/^$/d; s#^https?://##; s#/.*$##; s/^www\.//; s/^\*?\.//; s/[.,;]+$//' \
    | tr '[:upper:]' '[:lower:]' \
    | awk '!seen[$0]++')

# Валидация каждого домена: только a-z A-Z 0-9 . - и корректная форма.
# Это защищает pivas-list-файлы от мусора, обрезает попытки передать
# аргументы типа "--help", "-rf", "$(...)", и символы ломающие awk/jq.
clean_domains=""
for d in $domains; do
    # punycode/ascii-only, не начинается/заканчивается на - или .
    if echo "$d" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$' \
       && echo "$d" | grep -q '\.' \
       && ! echo "$d" | grep -Eq '(^[-.]|[-.]$|\.\.|\.-|-\.)'; then
        clean_domains="$clean_domains $d"
    else
        echo "Отклонён недопустимый домен: $d"
    fi
done
if [ -z "$clean_domains" ]; then
    echo "Ни одного валидного домена не передано."
    exit 1
fi

# shellcheck disable=SC2086
pivas vless "${slot}-${act}" $clean_domains 2>&1 | sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g'
