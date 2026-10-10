# Pivas 1.2.0-beta.1

[Русский](RELEASE_NOTES.md)

Fixed startup on installations missing `S56dnsmasq` or `S09dnscrypt-proxy2`. Pivas restores these scripts from templates and keeps existing DNS settings. If a dependency itself is damaged, it reports which package needs repair.

- Added `pivas repair-dns`.
- Diagnostic reports include DNS service status.
- The web interface shows the reason startup failed.

## Installation

Download the installer for your architecture and copy it to `/opt/tmp/` without renaming it. Over SSH, run `opkg update`, then the matching command below.

| Entware architecture | Installer | Command |
| --- | --- | --- |
| `mipsel-3.4` | [install-pivas-full-mipsel.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-mipsel.sh) | `sh /opt/tmp/install-pivas-full-mipsel.sh` |
| `aarch64-3.10` | [install-pivas-full-aarch64.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-aarch64.sh) | `sh /opt/tmp/install-pivas-full-aarch64.sh` |

Install Entware and the required KeeneticOS components first. See the [installation guide](../README.en.md#installation). Configure a connection and start Pivas after installation. Enable the web interface and bot separately.

## Updating

Download `pivas-full_1.2.0-beta.1_*.ipk` for your architecture. It updates Pivas, the web interface, bot and Xray together. [Telegram and SSH instructions](UPDATING.md) are available in Russian.

The package includes Xray 26.3.27. Component versions and checksums are in `manifest.json`; use `SHA256SUMS` to verify downloads.

This is a beta release. Routing covers IPv4; Hysteria 2 requires UDP. For XHTTP, remove a duplicate `mode` field from `extra` if it is already set in the URL. [Troubleshooting](TROUBLESHOOTING.md).

[Component licenses](../THIRD_PARTY_NOTICES.md). Upstream Telegram bot redistribution permission is still unresolved.
