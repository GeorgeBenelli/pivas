#!/bin/sh

# All state writers share one lock; nested calls retain its owner token.
if [ "${PIVAS_LOCK_ENTRY:-}" != "$0" ]; then
    exec /opt/bin/python3 /opt/apps/pivas/bin/main/runtime.py try-locked "$0" "$@"
fi
unset PIVAS_LOCK_ENTRY


if [ "${1}" = 'start' ] ; then
	. /opt/apps/pivas/bin/libs/ndm

	# стартуем ipset'ы, используемые DNS-серверами
	# до старта самих DNS-серверов
	ip4__ipset__create_list

	# Keenetic не вызывает /opt/etc/init.d/rc.unslung сам — поэтому без
	# этого блока S95pivas-routes / S96pivas / S98telegram4pivas / S99pivas-web
	# на boot не стартуют, и pipeline (chain PIVAS_MARK + jump + rule + route)
	# не собирается. Запускаем в фоне с задержкой, чтобы дать NDM поднять
	# Proxy21/xray и интерфейс t2s21.
	if [ -x /opt/etc/init.d/rc.unslung ]; then
		(sleep 15 && /opt/etc/init.d/rc.unslung start) >/dev/null 2>&1 &
	fi
fi
