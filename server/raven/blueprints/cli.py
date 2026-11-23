"""`flask raven ...` and `flask translate ...` commands (registered by create_app in CLI mode)."""

import os
import shlex
import subprocess
import sys

import click
from flask import current_app as app
from flask import g
from flask.cli import with_appcontext

from raven.certificate_authority import CertificateAuthority
from raven.defaultconfig import DefaultConfig
from raven.extensions import logger

_X_ARG_HELP = "Additional arguments consumed by custom env.py scripts"


@click.group(help="Raven server administration: certificate authority, certificates and config")
@click.option("-x", "--x-arg", multiple=True, help=_X_ARG_HELP)
@with_appcontext
def raven_cli(x_arg):
    g.x_arg = x_arg


@raven_cli.command()
@with_appcontext
def create_ca():
    """Create the CA unless one exists, then make sure its CRL is in place (safe to re-run)."""
    from raven import cert_revocation

    authority = CertificateAuthority(logger, app)
    already_there = authority.check_if_ca_exists()
    if already_there:
        logger.warning("Certificate authority already exists")
    else:
        logger.info("Creating certificate authority...")
        authority.create_ca()

    config = app.config
    cert_revocation.ensure_crl(config["RAVEN_CA_FOLDER"], config["RAVEN_CA_PASSWORD"], logger)
    sys.exit()


@raven_cli.command()
@click.argument("cn")
@with_appcontext
def issue_certificate(cn):
    ca = CertificateAuthority(logger, app)
    if not ca.check_if_ca_exists():
        return
    # A client certificate's data package names the server it was requested from, which needs a
    # request; from the command line, use a stand-in one for the configured domain.
    host = app.config.get("RAVEN_DOMAIN") or "localhost"
    with app.test_request_context(base_url=f"https://{host}/"):
        ca.issue_certificate(cn)


@raven_cli.command()
@with_appcontext
def issue_server_certificate():
    ca = CertificateAuthority(logger, app)
    if ca.check_if_ca_exists():
        ca.issue_certificate("raven", True)


@raven_cli.command()
@click.option("--overwrite", is_flag=True, default=False)
@with_appcontext
def generate_config(overwrite):
    from raven.config_file import write_default_config

    app.config.from_object(DefaultConfig)
    path = os.path.join(app.config["RAVEN_DATA_FOLDER"], "config.yml")
    if os.path.exists(path) and not overwrite:
        logger.warning("config.yml already exists")
        return
    logger.info("Creating config.yml")
    write_default_config(path)


@click.group(help="Create new translations")
@click.option("-x", "--x-arg", multiple=True, help=_X_ARG_HELP)
@with_appcontext
def translate(x_arg):
    g.x_arg = x_arg


def _pybabel(command: str, failure: str):
    if subprocess.call(command, shell=True):
        raise RuntimeError(failure)


def _extract():
    _pybabel("pybabel extract -F babel.cfg -k _l -o messages.pot .", "extract command failed")


@translate.command()
def update():
    _extract()
    _pybabel("pybabel update -i messages.pot -d translations", "update command failed")
    os.remove("messages.pot")


@translate.command()
def compile():
    _pybabel("pybabel compile -d translations", "compile command failed")


@translate.command(help="Initialize a new language.")
@click.argument("lang")
def init(lang):
    _extract()
    _pybabel(
        f"pybabel init -i messages.pot -d translations -l {shlex.quote(lang)}",
        "init command failed",
    )
    os.remove("messages.pot")
