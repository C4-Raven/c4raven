"""The running server's version.

A wheel built for the offline .deb has its version stamped into raven/__init__.py at build time
(poetry-dynamic-versioning). A server installed from a git checkout (install.sh, the updater) runs
the source tree as-is, where __init__.py keeps the "0.0.0" placeholder, so the version comes from
the nearest release tag instead: "2.4.0" on a tagged commit, "2.4.0+3.g5da52d5" three commits later.
"""

import os
import re
import subprocess

PLACEHOLDER = "0.0.0"
_DESCRIBE = re.compile(r"^v?(?P<base>\d+(?:\.\d+)*)-(?P<distance>\d+)-g(?P<sha>[0-9a-f]+)$")


def from_describe(output: str) -> str | None:
    """The version for `git describe --tags --long` output like "v2.4.0-3-g5da52d5", or None."""
    match = _DESCRIBE.match(output.strip())
    if not match:
        return None
    if match["distance"] == "0":
        return match["base"]
    return f"{match['base']}+{match['distance']}.g{match['sha']}"


def _git_describe(directory: str) -> str | None:
    try:
        result = subprocess.run(
            # safe.directory: the service user may not own the checkout
            ["git", "-c", f"safe.directory={directory}", "describe", "--tags", "--long", "--match", "v[0-9]*"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def resolve(stamped: str) -> str:
    """`stamped` when a build stamped it, else the version from the checkout's git tags."""
    if stamped != PLACEHOLDER:
        return stamped
    checkout = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(checkout, ".git")):
        described = _git_describe(checkout)
        version = from_describe(described) if described else None
        if version:
            return version
    return stamped
