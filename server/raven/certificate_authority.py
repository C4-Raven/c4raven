"""The server's private certificate authority.

Everything is done with the openssl CLI. The CA password only ever reaches openssl through the
RAVEN_CA_PASS environment variable (-passin/-passout env:RAVEN_CA_PASS), never on a command line.
"""

import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import unicodedata
import uuid
import zipfile
from urllib.parse import urlparse
from xml.sax.saxutils import escape, quoteattr

from flask import request

from raven.ca_config import ca_config, server_config

PASS_ENV = "RAVEN_CA_PASS"
PASS_ARG = f"env:{PASS_ENV}"

INNER_FOLDER = "5c2bfcae3d98c9f4d262172df99ebac5"
OUTER_FOLDER = "80b828699e074a239066d454a76284eb"
TRUSTSTORE_FILENAME = "truststore-root.p12"

# Dots deliberately unescaped (any character), as before
_IP_RE = re.compile(r"^[0-9]{1,3}.[0-9]{1,3}.[0-9]{1,3}.[0-9]{1,3}$")
_CN_EXTRA_CHARS = frozenset("._@-")


def _q(value) -> str:
    return shlex.quote(str(value))


class CertificateAuthority:
    def __init__(self, logger, app):
        self.logger = logger
        self.app = app

    # ------------------------------------------------------------------ config helpers

    @property
    def _config(self):
        return self.app.config

    @property
    def ca_folder(self) -> str:
        return self._config["RAVEN_CA_FOLDER"]

    def _path(self, *parts) -> str:
        return os.path.join(self.ca_folder, *parts)

    def _cert_dir(self, common_name) -> str:
        return self._path("certs", common_name)

    # ------------------------------------------------------------------ validation

    @staticmethod
    def validate_common_name(common_name):
        """The CN ends up in file paths and openssl arguments: allow letters, digits and ._@- only."""
        ok = (
            isinstance(common_name, str)
            and 1 <= len(common_name) <= 255
            and common_name[0] not in ".-"
            and ".." not in common_name
            and all(
                ch in _CN_EXTRA_CHARS or unicodedata.category(ch)[0] in ("L", "N")
                for ch in common_name
            )
        )
        if not ok:
            raise ValueError(f"Unsafe certificate common name: {common_name!r}")
        return common_name

    def validate_days(self, days) -> int:
        if days is None:
            return int(self._config["RAVEN_CA_EXPIRATION_TIME"])
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            raise ValueError(f"Invalid certificate validity: {days!r}")
        return days

    @staticmethod
    def escape_subject_value(value) -> str:
        """Backslash-escape the characters openssl's -subj parser treats specially."""
        return re.sub(r"([\\/+=])", r"\\\1", str(value))

    # ------------------------------------------------------------------ running openssl

    def _env(self):
        env = dict(os.environ)
        env[PASS_ENV] = str(self._config.get("RAVEN_CA_PASSWORD") or "")
        return env

    def _debug(self, message):
        if self.logger:
            self.logger.debug(message)

    def _run(self, command: str, failure: str):
        self._debug(command)
        exit_code = subprocess.call(command, shell=True, env=self._env())
        if exit_code != 0:
            raise Exception(f"{failure} Exit code {exit_code}")

    def _pkcs12_legacy_flag(self) -> str:
        # `openssl list -providers` only exists in OpenSSL 3, which needs -legacy for ATAK's p12s
        exit_code = subprocess.call(
            "openssl list -providers",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return " -legacy" if exit_code == 0 else ""

    def check_if_ca_exists(self) -> bool:
        return os.path.exists(self._path("ca.pem"))

    # ------------------------------------------------------------------ the CA itself

    def create_ca(self):
        if self.check_if_ca_exists():
            self._debug("The certificate authority already exists")
            return

        folder = self.ca_folder
        os.makedirs(folder, exist_ok=True)
        config_path = self._path("ca_config.cfg")
        with open(config_path, "w") as f:
            f.write(ca_config)

        ca_name = self._config["RAVEN_CA_NAME"]
        key = self._path("ca-do-not-share.key")
        ca_pem = self._path("ca.pem")
        trusted = self._path("ca-trusted.pem")
        subject = self._config["RAVEN_CA_SUBJECT"] + "/CN=" + self.escape_subject_value(ca_name)

        self._run(
            f"openssl req -new -sha256 -x509 -days {_q(self._config['RAVEN_CA_EXPIRATION_TIME'])}"
            f" -extensions v3_ca -keyout {_q(key)} -out {_q(ca_pem)} -passout {PASS_ARG}"
            f" -config {_q(config_path)} -subj {_q(subject)}",
            "Failed to create ca.pem.",
        )
        self._run(
            f"openssl x509 -in {_q(ca_pem)} -addtrust clientAuth -addtrust serverAuth"
            f" -setalias {_q(ca_name)} -out {_q(trusted)}",
            "Failed to add trust to CA.",
        )
        legacy = self._pkcs12_legacy_flag()
        self._run(
            f"openssl pkcs12{legacy} -export -in {_q(trusted)}"
            f" -out {_q(self._path(TRUSTSTORE_FILENAME))} -passout {PASS_ARG} -nokeys"
            f" -caname {_q(ca_name)}",
            "Failed to export truststore.",
        )

        open(self._path("crl_index.txt"), "a").close()
        with open(self._path("crl_index.txt.attr"), "w") as f:
            f.write("unique_subject = no")

        # ca_config uses paths relative to the CA folder
        self._run(
            f"cd {_q(folder)} && openssl ca -config {_q(config_path)} -gencrl -keyfile {_q(key)}"
            f" -passin {PASS_ARG} -cert {_q(ca_pem)} -out {_q(self._path('ca.crl'))}",
            "Failed to create crl.",
        )

        self.issue_certificate("raven", True)
        if self.logger:
            self.logger.info(
                "Certificate authority created successfully. "
                "You may need to restart nginx if it's proxying SSL requests."
            )

    # ------------------------------------------------------------------ issuing

    def issue_certificate(self, common_name, server=False, days=None):
        self.validate_common_name(common_name)
        days = self.validate_days(days)
        if not self.check_if_ca_exists():
            raise FileNotFoundError("ca.pem not found")

        cert_dir = self._cert_dir(common_name)
        if os.path.exists(cert_dir):
            shutil.rmtree(cert_dir)
        os.makedirs(cert_dir)

        key = os.path.join(cert_dir, f"{common_name}.key")
        csr = os.path.join(cert_dir, f"{common_name}.csr")
        pem = os.path.join(cert_dir, f"{common_name}.pem")
        p12 = os.path.join(cert_dir, f"{common_name}.p12")
        subject = self._config["RAVEN_CA_SUBJECT"] + "/CN=" + self.escape_subject_value(common_name)

        self._run(
            f"openssl req -new -newkey rsa:3072 -sha256 -keyout {_q(key)} -passout {PASS_ARG}"
            f" -out {_q(csr)} -subj {_q(subject)} -config {_q(self._path('ca_config.cfg'))}",
            "Failed to create csr.",
        )
        with open(csr, "rb") as f:
            csr_bytes = f.read()
        self.sign_csr(csr_bytes, common_name, server, days=days)

        legacy = self._pkcs12_legacy_flag()
        self._run(
            f"openssl pkcs12{legacy} -export -in {_q(pem)} -inkey {_q(key)} -out {_q(p12)}"
            f" -name {_q(common_name)} -CAfile {_q(self._path('ca.pem'))}"
            f" -passin {PASS_ARG} -passout {PASS_ARG}",
            "Failed to export p12 key.",
        )
        os.chmod(key, 0o600)

        if server:
            nopass = os.path.join(cert_dir, f"{common_name}.nopass.key")
            self._run(
                f"openssl rsa -in {_q(key)} -passin {PASS_ARG} -out {_q(nopass)}",
                "Failed to remove server key password.",
            )
            os.chmod(nopass, 0o600)
            self._run(
                f"openssl x509 -pubkey -in {_q(pem)}"
                f" -out {_q(os.path.join(cert_dir, f'{common_name}.pub'))}",
                "Failed to generate server's public key.",
            )
            return None

        return self.generate_zip(common_name)

    def sign_csr(self, csr_bytes, common_name, server=False, subject=None, days=None) -> bytes:
        self.validate_common_name(common_name)
        days = self.validate_days(days)

        cert_dir = self._cert_dir(common_name)
        os.makedirs(cert_dir, exist_ok=True)
        csr = os.path.join(cert_dir, f"{common_name}.csr")
        pem = os.path.join(cert_dir, f"{common_name}.pem")
        with open(csr, "wb") as f:
            f.write(csr_bytes)

        if server:
            alt_name_field = "IP.1" if _IP_RE.match(common_name) else "DNS.1"
            extfile = os.path.join(cert_dir, f"{common_name}_config.cfg")
            with open(extfile, "w") as f:
                f.write(
                    server_config.render(alt_name_field=alt_name_field, common_name=common_name)
                )
            extensions = "server"
        else:
            extfile = self._path("ca_config.cfg")
            extensions = "client"

        # Revocation is by serial number, so serials must be unique: a random 63-bit integer
        serial = secrets.randbits(63) or 1

        command = (
            f"openssl x509 -sha256 -req -days {days} -in {_q(csr)} -CA {_q(self._path('ca.pem'))}"
            f" -CAkey {_q(self._path('ca-do-not-share.key'))} -out {_q(pem)} -set_serial {serial}"
            f" -passin {PASS_ARG} -extensions {extensions} -extfile {_q(extfile)}"
        )
        if subject:
            # Overrides the CSR's subject (OpenSSL >= 3.0)
            command += f" -subj {_q(subject)}"
        self._run(command, "Failed to sign csr.")

        if server:
            with open(self._path("ca.pem"), "rb") as ca, open(pem, "ab") as out:
                out.write(ca.read())

        with open(pem, "rb") as f:
            return f.read()

    # ------------------------------------------------------------------ enrollment package

    def _preference_xml(self, common_name, host) -> str:
        password = str(self._config.get("RAVEN_CA_PASSWORD") or "")
        https_port = self._config.get("RAVEN_MARTI_HTTPS_PORT")
        streaming_port = self._config.get("RAVEN_SSL_STREAMING_PORT")
        truststore_location = f"/storage/emulated/0/atak/cert/{TRUSTSTORE_FILENAME}"

        def entry(key, kind, value):
            if kind == "Boolean":
                value = "true" if value else "false"
            return (
                f'    <entry key={quoteattr(key)} class="class java.lang.{kind}">'
                f"{escape(str(value))}</entry>\n"
            )

        streams = [
            entry("count", "Integer", 1),
            entry("description0", "String", f"Raven_{host}"),
            entry("enabled0", "Boolean", True),
            entry("connectString0", "String", f"{host}:{streaming_port}:ssl"),
        ]
        preferences = [
            entry("deviceProfileEnableOnConnect", "Boolean", True),
            entry("displayServerConnectionWidget", "Boolean", True),
            entry("caLocation", "String", truststore_location),
            entry("caPassword", "String", password),
            entry("clientPassword", "String", password),
            entry(
                "certificateLocation", "String", f"/storage/emulated/0/atak/cert/{common_name}.p12"
            ),
            entry("appMgmtEnableUpdateServer", "Boolean", True),
            entry("atakUpdateServerUrl", "String", f"https://{host}:{https_port}/api/packages"),
            entry("repoStartupSync", "Boolean", True),
            entry("updateServerCaLocation", "String", truststore_location),
            entry("updateServerCaPassword", "String", password),
        ]
        return (
            "<?xml version='1.0' standalone='yes'?>\n"
            "<preferences>\n"
            '  <preference version="1" name="cot_streams">\n'
            + "".join(streams)
            + "  </preference>\n"
            '  <preference version="1" name="com.atakmap.app_preferences">\n'
            + "".join(preferences)
            + "  </preference>\n"
            "</preferences>\n"
        )

    @staticmethod
    def _manifest_xml(name, zip_entries) -> str:
        contents = "".join(
            f'    <Content ignore="false" zipEntry={quoteattr(entry)}/>\n' for entry in zip_entries
        )
        return (
            '<MissionPackageManifest version="2">\n'
            "  <Configuration>\n"
            f'    <Parameter name="uid" value={quoteattr(str(uuid.uuid4()))}/>\n'
            f'    <Parameter name="name" value={quoteattr(name)}/>\n'
            '    <Parameter name="onReceiveDelete" value="true"/>\n'
            "  </Configuration>\n"
            "  <Contents>\n"
            f"{contents}"
            "  </Contents>\n"
            "</MissionPackageManifest>\n"
        )

    def _zip_tree(self, zip_file, base, folder, log=False):
        """Adds every file under <base>/<folder> to zip_file, named relative to base (no chdir)."""
        root = os.path.join(base, folder)
        for current, dirs, files in os.walk(root):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(current, name)
                arcname = os.path.relpath(path, base).replace(os.sep, "/")
                if log and self.logger:
                    self.logger.debug(f"Adding {arcname} to the package")
                zip_file.write(path, arcname)

    def generate_zip(self, common_name) -> list[str]:
        """Builds the ATAK/WinTAK enrollment data package <cn>_CONFIG.zip. Needs a request
        context: the server address is the host the request came in on."""
        from raven.blueprints.raven_api.onboard_api import export_onboard_truststore

        self.validate_common_name(common_name)
        host = urlparse(request.url_root).hostname
        cert_dir = self._cert_dir(common_name)
        user_p12 = os.path.join(cert_dir, f"{common_name}.p12")
        inner_zip = os.path.join(cert_dir, f"{common_name}.zip")
        outer_name = f"{common_name}_CONFIG.zip"

        # Exported fresh, so it opens with the CA password the preferences give
        truststore = export_onboard_truststore(
            self.ca_folder, self._config["RAVEN_CA_PASSWORD"], self._config["RAVEN_CA_NAME"]
        )

        inner_entries = [
            f"{INNER_FOLDER}/preference.pref",
            f"{INNER_FOLDER}/{TRUSTSTORE_FILENAME}",
            f"{INNER_FOLDER}/{common_name}.p12",
        ]
        try:
            with zipfile.ZipFile(inner_zip, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(inner_entries[0], self._preference_xml(common_name, host))
                z.writestr(inner_entries[1], truststore)
                z.write(user_p12, inner_entries[2])
                z.writestr(
                    "MANIFEST/manifest.xml", self._manifest_xml(f"Raven_{host}", inner_entries)
                )

            outer_entry = f"{OUTER_FOLDER}/{common_name}.zip"
            # Written under a temporary name first, so a failure never leaves half a package
            fd, tmp_outer = tempfile.mkstemp(dir=cert_dir, suffix=".zip.tmp")
            os.close(fd)
            try:
                with zipfile.ZipFile(tmp_outer, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(inner_zip, outer_entry)
                    z.writestr(
                        "MANIFEST/manifest.xml",
                        self._manifest_xml(f"Raven_{host}_CONFIG", [outer_entry]),
                    )
                os.replace(tmp_outer, os.path.join(cert_dir, outer_name))
            finally:
                if os.path.exists(tmp_outer):
                    os.remove(tmp_outer)
        finally:
            if os.path.exists(inner_zip):
                os.remove(inner_zip)

        return [outer_name]
