# Pivas custom31 — DNS service recovery

[Русский](RELEASE_NOTES.md) · **English**

Installation and startup now detect missing dnsmasq and dnscrypt-proxy2 init scripts and restore them from local templates. This addresses installations where the packages are registered as installed but `pivas start` fails with `S56dnsmasq: not found` or a missing `S09dnscrypt-proxy2`.

The new `pivas repair-dns` command checks these files. Diagnostic reports include their status, and the web interface shows the reason startup failed. Existing DNS settings are preserved. Missing binaries and damaged dependencies still require repair as described in the error message.

## Components

- Pivas `1.1.9_beta-10-25-custom25`.
- Web interface `1.0-custom20`, Telegram bot `1.2-custom14`.
- Xray `26.3.27-2`, Xray wrapper `26.3.27-1-custom4`.
- QUIC helper `0.1-1`.

## Downloads

- `pivas-custom31-mipsel.tar.gz` — Entware `mipsel-3.4`.
- `pivas-custom31-aarch64.tar.gz` — Entware `aarch64-3.10`.
- `pivas-custom31-common.tar.gz` — architecture-independent IPKs for modular installations.
- `pivas-full_...ipk` — the complete package for the matching architecture, suitable for SSH updates or a compatible Telegram bot.
- `SHA256SUMS` and `manifest.json` — checksums for release downloads and the individual packages/installers.

For a fresh installation, use `install-pivas-full.sh` from the archive for your architecture. Entware, EXT, Netfilter and Proxy client are prerequisites. Configure your connection URLs and explicitly enable Pivas after installation. Enable the web interface and bot separately when needed.

## Known limitations

Routing targets IPv4. IPv6 and application-level DoH are not automatically covered. In custom31, the XHTTP importer rejects a duplicate `mode` inside the `extra` JSON object. Hysteria 2 requires working UDP connectivity. MIPSel uses a Go 1.26.6 compatibility build; AArch64 uses the official Xray binary.

This is a prerelease. Local preparation checks are recorded in `VALIDATION.md` (Russian); automated tests are distinct from validation on individual routers. Before publicly distributing the complete bundle, resolve the upstream Telegram bot licensing question described in `THIRD_PARTY_NOTICES.md` and `LICENSE`.
