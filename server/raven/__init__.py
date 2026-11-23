# Build-time version stamp (poetry-dynamic-versioning rewrites both lines).
__version__ = "0.0.0"
__version_tuple__ = (0, 0, 0)

# Not stamped (a git checkout rather than a built wheel): use the release tags (raven/_version.py)
from raven._version import resolve as _resolve  # noqa: E402

__version__ = _resolve(__version__)
