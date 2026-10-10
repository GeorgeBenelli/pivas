# Pivas 1.2.0-beta.1 — DNS service recovery

[Русский](RELEASE_NOTES.md) · **English**

Installation and startup now detect missing dnsmasq and dnscrypt-proxy2 init scripts and restore them from local templates. This addresses installations where the packages are registered as installed but `pivas start` fails with `S56dnsmasq: not found` or a missing `S09dnscrypt-proxy2`.

The new `pivas repair-dns` command checks these files. Diagnostic reports include their status, and the web interface shows the reason startup failed. Existing DNS settings are preserved. Missing binaries and damaged dependencies still require repair as described in the error message.

## Components

- Pivas `1.1.9_beta-10-25-custom25`.
- Web interface `1.0-custom20`, Telegram bot `1.2-custom14`.
- Xray `26.3.27-2`, Xray wrapper `26.3.27-1-custom4`.
- QUIC helper `0.1-1`.

### Download the full installer

| Entware architecture | Direct download | Run over SSH |
| --- | --- | --- |
| `mipsel-3.4` | [Download install-pivas-full-mipsel.sh — MIPSel](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-mipsel.sh) | `sh /opt/tmp/install-pivas-full-mipsel.sh` |
| `aarch64-3.10` (including KN-1811) | [Download install-pivas-full-aarch64.sh — AArch64](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-aarch64.sh) | `sh /opt/tmp/install-pivas-full-aarch64.sh` |

Each installer embeds the complete package. Check `opkg print-architecture`, download the matching file and copy it to **`/opt/tmp/`** on the router, keeping its original filename. No renaming is needed. A fresh installation needs only that `.sh` file and access to the Entware repository.

[All 1.2.0-beta.1 downloads](https://github.com/Georgy-Benelli/pivas/releases/tag/v1.2.0-beta.1) · [SHA256SUMS](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/SHA256SUMS)

## Release files

- `install-pivas-full-mipsel.sh` / `install-pivas-full-aarch64.sh` — the complete installer for a fresh installation.
- `pivas-full_...ipk` — the complete package for updates over SSH or Telegram.
- `SHA256SUMS` and `manifest.json` — checksums and component versions.
- `LICENSE` and `THIRD_PARTY_NOTICES.md` — licensing and component provenance.

Choose the architecture shown by `opkg print-architecture`. Entware, EXT, Netfilter and Proxy client are prerequisites. Configure the connection URLs and explicitly enable Pivas after installation; enable the web interface and bot when needed.

## Known limitations

Routing targets IPv4. IPv6 and application-level DoH are not automatically covered. In 1.2.0-beta.1, the XHTTP importer rejects a duplicate `mode` inside the `extra` JSON object. Hysteria 2 requires working UDP connectivity. MIPSel uses a Go 1.26.6 compatibility build; AArch64 uses the official Xray binary.

This is a prerelease. Local preparation checks are recorded in `VALIDATION.md` (Russian); automated tests are distinct from validation on individual routers. Before publicly distributing the complete bundle, resolve the upstream Telegram bot licensing question described in `THIRD_PARTY_NOTICES.md` and `LICENSE`.
