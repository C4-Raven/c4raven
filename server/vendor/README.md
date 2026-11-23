# Vendored wheels

`adsbxcot` was deleted from PyPI (the project returns 404 as of October
2026), so `poetry install` could no longer resolve it and every fresh
install failed. This is the exact wheel the lock pinned before, checksum
unchanged:

| Wheel | sha256 |
|---|---|
| `adsbxcot-6.0.4-py3-none-any.whl` | `5f134f502d18c1cb40bd23be37863c0b42edc081221fb623e8f65f7857313148` |

pyproject.toml refers to it as a path dependency. Its own
dependencies (`pytak`, `aircot`, `aiohttp`) are still on PyPI.
