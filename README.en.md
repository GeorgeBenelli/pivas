# Pivas

[Русский](README.md) · English

Pivas routes selected websites and IP networks through Xray on a Keenetic router. Other traffic uses the regular connection. Manage connections and routing lists from the web interface or Telegram.

**1.2.0-beta.1** · Xray 26.3.27 · MIPSel / AArch64

![Pivas](docs/images/dashboard-dark-demo.png)

[Light theme](docs/images/dashboard-light-demo.png) · [Mobile view](docs/images/dashboard-mobile-demo.png)

## Features

- Two connections with separate routing lists. Swap their connection URLs with one button.
- VLESS: TCP + Reality, gRPC + TLS, XHTTP + TLS/Reality. Hysteria 2 with optional Salamander.
- Domain groups, individual domains, IPv4 addresses and CIDR networks. Domain rules include subdomains.
- Listed domains use DNS through their slot; other domains use Keenetic's regular DNS.
- Exclude up to 10 devices using MAC addresses.
- Manage groups and slots, get diagnostics and install updates through Telegram.
- Light and dark themes, encrypted configuration backups and resource usage monitoring.

## Installation

Install Entware and these KeeneticOS components: Proxy client, Open Package support, Netfilter and EXT filesystem support. Use EXT4 for a USB drive. [Detailed guide in Russian](docs/INSTALL.md).

Check the architecture over Entware SSH:

```sh
opkg print-architecture
```

Download the matching installer and copy it to `/opt/tmp/`, keeping the filename. Run `opkg update`, then **one** command from the table:

| Entware architecture | Installer | Command |
| --- | --- | --- |
| `mipsel-3.4` | [install-pivas-full-mipsel.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-mipsel.sh) | `sh /opt/tmp/install-pivas-full-mipsel.sh` |
| `aarch64-3.10` | [install-pivas-full-aarch64.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-aarch64.sh) | `sh /opt/tmp/install-pivas-full-aarch64.sh` |

The installer asks for connection URLs, a bot token and an administrator ID. Press Enter to skip a field and fill it in later. Enable the web interface, bot and routing separately.

To continue setup in the web interface:

```sh
pivas web on 'YOUR_PASSWORD'
```

Open `http://ROUTER_ADDRESS:8888` and sign in as `admin`. Add a connection URL, create a group and enable Pivas. From SSH, use `pivas start`.

For updates, download the full `.ipk` for your architecture from the [release page](https://github.com/Georgy-Benelli/pivas/releases/tag/v1.2.0-beta.1). In the bot, select «Сервис → Обновить пакет». [Update guide](docs/UPDATING.md).

## Documentation

The detailed guides are in Russian: [Configuration](docs/SETUP.md) · [DNS and routing](docs/ARCHITECTURE.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Building](docs/DEVELOPMENT.md). [Release notes](docs/RELEASE_NOTES.en.md) are also available in English.

Routing covers IPv4. IPv6 and browser DoH can bypass the rules. The web interface is for local networks; do not expose port 8888 to the internet. [Security](SECURITY.md).

## Credits

Pivas is based on [KVAS](https://github.com/qzeleza/kvas) and [telegram4kvas](https://github.com/dnstkrv/telegram4kvas), and uses Xray and pyTelegramBotAPI. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [LICENSE](LICENSE) for component licenses and the unresolved upstream bot redistribution permission.
