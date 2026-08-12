"""TLS (secure DICOM) helpers.

Builds ``ssl.SSLContext`` objects for SCU (outgoing) and SCP (receiver)
associations. DICOM-over-TLS peers are frequently identified by IP rather than a
certificate hostname, so hostname checking is disabled while certificate-chain
verification against a CA (when provided) is kept.
"""

from __future__ import annotations

import ssl


def client_context(ca_file: str = "", cert_file: str = "", key_file: str = "",
                   verify: bool = True) -> ssl.SSLContext:
    """SSL context for an outgoing (SCU) association.

    * ``verify`` False -> encrypt but do not validate the server certificate.
    * ``ca_file`` set   -> validate the server certificate against that CA.
    * ``verify`` True, no ca_file -> validate against the system trust store.
    * ``cert_file``/``key_file`` -> present a client certificate (mutual TLS).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False  # PACS/VNA certs rarely match the IP used
    if not verify:
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx.verify_mode = ssl.CERT_REQUIRED
        if ca_file:
            ctx.load_verify_locations(cafile=ca_file)
        else:
            ctx.load_default_certs()
    if cert_file and key_file:
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return ctx


def server_context(cert_file: str, key_file: str, ca_file: str = "",
                   require_client_cert: bool = False) -> ssl.SSLContext:
    """SSL context for the receiver (SCP). Requires a server cert + key."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    if ca_file:
        ctx.load_verify_locations(cafile=ca_file)
    if require_client_cert:
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx
