"""OpenSSL configuration for the server's CA.

`ca_config` is written to <RAVEN_CA_FOLDER>/ca_config.cfg. Its paths are relative (dir = .), so
`openssl ca` must run with the CA folder as its working directory. `server_config` adds a
subjectAltName to the [server] extensions for server certificates.
"""

from jinja2 import Template

_CA_SECTIONS = """default_crl_days = 730

[ ca ]
default_ca = CA_default

[ CA_default ]
dir = .
certs = $dir
crl_dir = $dir/crl
database = $dir/crl_index.txt
default_md = default

[ req ]
default_bits = 3072
default_keyfile = ca.pem
distinguished_name = req_distinguished_name
x509_extensions = v3_ca

[ req_distinguished_name ]
countryName_min = 2
countryName_max = 2
commonName_max = 64

[ v3_ca ]
basicConstraints = critical,CA:TRUE
keyUsage = critical, cRLSign, keyCertSign

[ client ]
basicConstraints = critical,CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = critical, clientAuth
"""

_SERVER_SECTION = """
[ server ]
basicConstraints = critical,CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = critical, clientAuth, serverAuth
"""

ca_config = _CA_SECTIONS + _SERVER_SECTION

server_config = Template(_CA_SECTIONS + _SERVER_SECTION + """subjectAltName = @alt_names

[ alt_names ]
{{ alt_name_field }} = {{ common_name }}
""")
