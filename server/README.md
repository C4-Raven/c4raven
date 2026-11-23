<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/logo-black.png">
    <img src="docs/logo.png" alt="CROW Network by C4 Raven" width="480">
  </picture>
</p>

<h3 align="center">The security-hardened TAK server</h3>

<p align="center">
  Run your own Team Awareness Kit server for ATAK, iTAK, WinTAK and TAKX —
  with 2FA, a built-in certificate authority and a modern web UI.
</p>

<p align="center">
  <a href="https://c4raven.net"><img src="https://img.shields.io/badge/website-c4raven.net-1f2937?style=flat-square" alt="Website"></a>
  <a href="https://discord.gg/snGSXEMEgm"><img src="https://img.shields.io/badge/Discord-join%20the%20chat-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://www.instagram.com/c4ravenofficial/"><img src="https://img.shields.io/badge/Instagram-c4ravenofficial-E4405F?style=flat-square&logo=instagram&logoColor=white" alt="Instagram"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue?style=flat-square" alt="License: GPL-3.0-or-later"></a>
  <img src="https://img.shields.io/badge/python-3.10%E2%80%933.14-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10–3.14">
  <img src="https://img.shields.io/badge/Ubuntu-24.04%20%7C%2025.10-E95420?style=flat-square&logo=ubuntu&logoColor=white" alt="Ubuntu 24.04 | 25.10">
</p>

<p align="center">
  <a href="https://c4raven.net"><strong>Install</strong></a> ·
  <a href="https://c4raven.net"><strong>Update</strong></a> ·
  <a href="https://discord.gg/snGSXEMEgm"><strong>Changelog</strong></a> ·
  <a href="../ui"><strong>Raven UI</strong></a> ·
  <a href="https://discord.gg/snGSXEMEgm"><strong>Discord</strong></a> ·
  <a href="#community-and-support"><strong>Support</strong></a>
</p>

---

# Raven

Raven is the security-hardened TAK (Team Awareness Kit) server behind
[C4 Raven](https://c4raven.net), with the
[Raven UI](../ui) web interface.

> [!TIP]
> **Looking for the guides and support?** The User Guide, the Technical
> Documentation, the other supporting documents and help are all in our
> [Discord server](https://discord.gg/snGSXEMEgm).

| | |
| --- | --- |
| 🔐 **Secure by default** | Mandatory 2FA, built-in certificate authority, login lockout, audit trail |
| 📱 **Every TAK client** | ATAK, WinTAK, iTAK, TAKAware, TAKX, CloudTAK and PyTAK over TCP, TLS and UDP |
| 📷 **One-scan onboarding** | QR-code enrollment, even for devices reaching the server by a bare VPN IP |
| 🗂️ **Group isolation** | Groups decide who sees whom; data packages never leak between groups |
| 🧩 **Device management** | ATAK plugin updates (including from a linked TAK.gov account), device profiles, remote clear |
| 🎥 **Video built in** | Streaming and recording through MediaMTX, playable in the browser |
| 🌐 **Federation ready** | Link to other Raven and TAK servers through Federation Hub, managed from the web UI |

## For developers

How the server is built (processes, groups and visibility, the Federation
Hub proxy, running the tests) is in [docs/architecture.md](docs/architecture.md).

## Community and support

- **Discord**: questions, help and release news: [discord.gg/snGSXEMEgm](https://discord.gg/snGSXEMEgm)
- **Website**: [c4raven.net](https://c4raven.net)
- **Instagram**: [@c4ravenofficial](https://www.instagram.com/c4ravenofficial/)
- **Email**: [support@c4raven.net](mailto:support@c4raven.net)
- **Security issues**: please email rather than posting in Discord, so a fix
  can ship before the details are public

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)).

Raven is built on [OpenTAKServer](https://github.com/brian7704/OpenTAKServer)
by Brian Wallen and contributors (GPL-3.0), with our thanks, and has been
substantially extended by C4 Raven since August 2026. See [NOTICE](NOTICE).
