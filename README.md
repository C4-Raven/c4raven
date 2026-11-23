# Raven TAK Server: source code

This is the published source of **Raven**, a TAK server for ATAK, iTAK, WinTAK
and TAKX clients, by C4 Raven. The version here is **v2.5.21**.

> [!TIP]
> **Looking for the guides and support?** The User Guide, the Technical
> Documentation, the other supporting documents and help are all in our
> [Discord server](https://discord.gg/snGSXEMEgm).

It is published so anyone can read and audit the code Raven is built from,
and see the licence and copyright notices. Development happens elsewhere; this
repository gets one commit per release, tagged with the release version.

| Folder | What it is |
|---|---|
| [`server/`](server) | Backend: CoT routing, Marti API, certificate authority, data packages, missions, video, Federation Hub bridge (Python / Flask) |
| [`ui/`](ui) | Web interface (React / TypeScript / Mantine) |

`SOURCE-VERSIONS` records the exact commit each folder was taken from.

This repository does not include Raven's installer, packaging or install
bundles, and there are no downloads here. Raven is supplied by C4 Raven. To
get it, or for support, email support@c4raven.net.

## Licence

Raven is free software under the **GNU General Public License v3.0 or later**
([LICENSE](LICENSE)). It comes with no warranty.

Raven is built on [OpenTAKServer](https://github.com/brian7704/OpenTAKServer)
and [OpenTAKServer-UI](https://github.com/brian7704/OpenTAKServer-UI)
(© Brian Wallen and contributors, GPL-3.0), with our thanks, and has been
substantially extended by C4 Raven since August 2026. [NOTICE](NOTICE) and each folder's own `NOTICE`
give the full copyright details, including which parts are C4 Raven's
original work.

Every Raven install bundle includes the complete corresponding source of the
release it installs, and the licences of all third-party software inside it.
