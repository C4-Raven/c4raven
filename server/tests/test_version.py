"""raven/_version.py: the version a git checkout reports."""

import subprocess

import pytest

import raven
from raven import _version


@pytest.mark.parametrize(
    "described,expected",
    [
        ("v2.4.0-0-g5da52d5\n", "2.4.0"),
        ("v2.4.0-3-g5da52d5", "2.4.0+3.g5da52d5"),
        ("2.4.1-12-gabcdef0", "2.4.1+12.gabcdef0"),
        ("v2.4-1-g1234567", "2.4+1.g1234567"),
        ("", None),
        ("5da52d5", None),
        ("vnext-1-g1234567", None),
    ],
)
def test_from_describe(described, expected):
    assert _version.from_describe(described) == expected


def test_stamped_version_wins():
    assert _version.resolve("2.3.2") == "2.3.2"


def test_checkout_uses_git_tags(monkeypatch):
    monkeypatch.setattr(_version, "_git_describe", lambda directory: "v2.4.0-2-gabc1234\n")
    assert _version.resolve(_version.PLACEHOLDER) == "2.4.0+2.gabc1234"


def test_no_tags_keeps_placeholder(monkeypatch):
    monkeypatch.setattr(_version, "_git_describe", lambda directory: None)
    assert _version.resolve(_version.PLACEHOLDER) == _version.PLACEHOLDER


def test_git_missing(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert _version._git_describe("/nonexistent") is None


def test_package_exposes_a_version():
    assert isinstance(raven.__version__, str) and raven.__version__
