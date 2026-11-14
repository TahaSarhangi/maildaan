"""Per-account TLS trust for IMAP connections.

MailDaan never disables certificate verification globally. Every account
gets its own TLS configuration built on top of the normal verification
context (system trust store, CERT_REQUIRED, hostname check on). On top of
that, one account may carry:

* a custom trusted CA certificate (PEM) imported by the user, loaded into
  that account's context only, or
* the SHA-256 fingerprint of one server certificate the user inspected
  and explicitly trusted for this account (certificate pinning). The pin
  is verified during the TLS handshake of every connection, before any
  IMAP credential is sent; a changed certificate is rejected.

Trust settings live inside the account's own data folder
(tls_trust.json, optionally ca.pem) and are never shared between
accounts.
"""

import hashlib
import json
import os
import shutil
import socket
import ssl

import accounts
from imapclient import IMAPClient

TRUST_FILE = "tls_trust.json"
CA_FILE = "ca.pem"

_HEX = set("0123456789abcdef")


class CertificateRejected(Exception):
    """The presented certificate does not match this account's trust.

    Carries the certificate details gathered at connection time so the
    user can review exactly what the server presented.
    """

    def __init__(self, message, pin_mismatch=False, details=None):
        super().__init__(message)
        self.pin_mismatch = pin_mismatch
        self.details = details or {}


def _valid_fingerprint(value) -> str:
    value = (value or "").replace(":", "").replace(" ", "").strip().lower()
    if len(value) == 64 and all(c in _HEX for c in value):
        return value
    return ""


def load_trust(account) -> dict:
    """Trust settings for one account: {'ca_path': …} and/or
    {'pinned_sha256': …}. Empty when nothing has been configured."""
    if not account:
        return {}
    path = os.path.join(accounts.account_paths(account)["root"], TRUST_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    trust = {}
    ca_path = data.get("ca_path") or ""
    if ca_path and os.path.isfile(ca_path):
        trust["ca_path"] = ca_path
    pinned = _valid_fingerprint(data.get("pinned_sha256"))
    if pinned:
        trust["pinned_sha256"] = pinned
    return trust


def save_trust(account, **fields) -> None:
    """Atomically write trust fields for one account (None removes a field)."""
    root = accounts.account_paths(account)["root"]
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, TRUST_FILE)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    for key, value in fields.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_pin(account, sha256_hex: str) -> str:
    """Pin one server certificate (64 hex chars) for this account.

    Pinning replaces a previously imported custom CA: one explicit trust
    decision per account.
    """
    fingerprint = _valid_fingerprint(sha256_hex)
    if not fingerprint:
        raise ValueError("invalid SHA-256 fingerprint")
    save_trust(account, pinned_sha256=fingerprint, ca_path=None)
    return fingerprint


def save_ca(account, source_pem: str) -> str:
    """Validate a PEM certificate authority and copy it into the account
    folder so removing the original file cannot break the trust."""
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(cafile=source_pem)  # SSLError if not usable
    root = accounts.account_paths(account)["root"]
    os.makedirs(root, exist_ok=True)
    target = os.path.join(root, CA_FILE)
    shutil.copyfile(source_pem, target)
    save_trust(account, ca_path=target, pinned_sha256=None)
    return target


def _format_name(name) -> str:
    parts = []
    for rdn in name or ():
        for key, value in rdn:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


# ------------------------------------------------------------------ DER reader

_OID_NAMES = {
    "2.5.4.3": "CN", "2.5.4.6": "C", "2.5.4.7": "L", "2.5.4.8": "ST",
    "2.5.4.10": "O", "2.5.4.11": "OU", "1.2.840.113549.1.9.1": "emailAddress",
    "0.9.2342.19200300.100.1.25": "DC",
}


def _der_tlv(data: bytes, offset: int):
    """Split one DER TLV. Returns (tag, content, offset_after)."""
    tag = data[offset]
    o = offset + 1
    if (tag & 0x1F) == 0x1F:
        while data[o] & 0x80:
            o += 1
        o += 1
    length = data[o]
    o += 1
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(data[o:o + n], "big")
        o += n
    return tag, data[o:o + length], o + length


def _decode_oid(data: bytes) -> str:
    parts = [str(data[0] // 40), str(data[0] % 40)]
    stack = 0
    for byte in data[1:]:
        stack = (stack << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(str(stack))
            stack = 0
    return ".".join(parts)


def _decode_der_string(tag: int, data: bytes) -> str:
    if tag == 0x1E:  # BMPString
        return data.decode("utf-16-be", "replace")
    return data.decode("utf-8", "replace")


def _parse_name(name_der: bytes) -> str:
    """Render an X.509 Name (RDN sequence) as 'CN=…, O=…'."""
    out = []
    offset = 0
    while offset < len(name_der):
        _, rdn, offset = _der_tlv(name_der, offset)          # SET OF ATV
        rdn_offset = 0
        while rdn_offset < len(rdn):
            _, atv, rdn_offset = _der_tlv(rdn, rdn_offset)   # SEQUENCE
            _, oid_value, value_offset = _der_tlv(atv, 0)    # OID
            value_tag, value, _ = _der_tlv(atv, value_offset)
            label = _OID_NAMES.get(_decode_oid(oid_value), _decode_oid(oid_value))
            out.append(f"{label}={_decode_der_string(value_tag, value)}")
    return ", ".join(out)


def _parse_time(tag: int, data: bytes) -> str:
    """Render a UTCTime / GeneralizedTime value as 'YYYY-MM-DD HH:MM UTC'."""
    text = data.decode("ascii", "replace").strip()
    try:
        if tag == 0x17 and len(text) >= 11:      # UTCTime: YYMMDDHHMMSS(Z)
            year = int(text[:2]) + (1900 if int(text[:2]) >= 50 else 2000)
            fields = (f"{year:04d}", text[2:4], text[4:6], text[6:8], text[8:10])
        elif tag == 0x18 and len(text) >= 12:    # GeneralizedTime: YYYYMMDDHHMMSS(Z)
            fields = (text[:4], text[4:6], text[6:8], text[8:10], text[10:12])
        else:
            return text
        return "%s-%s-%s %s:%s UTC" % fields
    except ValueError:
        return text


def _cert_fields(der: bytes) -> dict:
    """Read subject, issuer and validity out of a DER certificate.

    The ssl module only parses the peer certificate when chain
    verification succeeds — which is exactly the case being inspected
    here — so the fields are read directly from the DER structure.
    """
    try:
        _, body, _ = _der_tlv(der, 0)                 # Certificate SEQUENCE
        _, tbs, _ = _der_tlv(body, 0)                 # tbsCertificate SEQUENCE
        offset = 0
        tag, _, offset = _der_tlv(tbs, offset)        # [0] version (v3) or serial
        if tag == 0xA0:
            _, _, offset = _der_tlv(tbs, offset)      # serialNumber
        _, _, offset = _der_tlv(tbs, offset)          # signature AlgorithmIdentifier
        _, issuer, offset = _der_tlv(tbs, offset)     # issuer Name
        _, validity, offset = _der_tlv(tbs, offset)   # validity
        _, subject, _ = _der_tlv(tbs, offset)         # subject Name
        vtag, vfirst, vnext = _der_tlv(validity, 0)
        _, vsecond, _ = _der_tlv(validity, vnext)
        return {
            "subject": _parse_name(subject),
            "issuer": _parse_name(issuer),
            "valid_from": _parse_time(vtag, vfirst),
            "valid_to": _parse_time(vtag, vsecond),
        }
    except (IndexError, ValueError):
        return {}


def inspect_certificate(host: str, port: int, timeout: float = 15.0) -> dict:
    """Fetch the certificate the server currently presents.

    The inspection socket performs no certificate verification and carries
    no IMAP traffic — not even a login attempt. It exists only so the user
    can see the certificate and decide whether to trust it.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    try:
        tls = ctx.wrap_socket(sock, server_hostname=host)
        try:
            der = tls.getpeercert(binary_form=True) or b""
            details = _cert_fields(der)
            details.update({
                "host": host,
                "port": int(port),
                "sha256": hashlib.sha256(der).hexdigest().upper(),
            })
            return details
        finally:
            try:
                tls.close()
            except OSError:
                pass
    finally:
        try:
            sock.close()
        except OSError:
            pass


class AccountTLS:
    """TLS configuration for exactly one IMAP account.

    imapclient calls ``ssl_context.wrap_socket(sock, server_hostname=host)``
    for every connection, which is where the pin is enforced — on the same
    connection that later carries the IMAP login, so a mismatching
    certificate can never reach the credential exchange.
    """

    def __init__(self, trust: dict):
        # The default path: full normal verification against the system
        # trust store. A custom CA is added to (never replaces) this store
        # for this account only.
        self._ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        ca_path = trust.get("ca_path") if trust else None
        if ca_path:
            self._ctx.load_verify_locations(cafile=ca_path)
        self._pinned_sha256 = _valid_fingerprint(
            (trust or {}).get("pinned_sha256"))
        self._pin_ctx = None
        if self._pinned_sha256:
            # Inner context used only to complete the handshake so the
            # presented certificate can be compared with the pin. The pin
            # match itself is the verification for this connection.
            self._pin_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._pin_ctx.check_hostname = False
            self._pin_ctx.verify_mode = ssl.CERT_NONE

    def wrap_socket(self, sock, **kwargs):
        if self._pin_ctx is None:
            return self._ctx.wrap_socket(sock, **kwargs)
        tls = self._pin_ctx.wrap_socket(sock, **kwargs)
        der = tls.getpeercert(binary_form=True) or b""
        fingerprint = hashlib.sha256(der).hexdigest()
        if fingerprint != self._pinned_sha256:
            try:
                tls.close()
            except OSError:
                pass
            raise CertificateRejected(
                "the certificate presented by the server does not match the "
                "certificate trusted for this account", pin_mismatch=True)
        return tls


def connect(host: str, port, trust: dict, timeout=60) -> IMAPClient:
    """Create an IMAPClient connected with one account's TLS settings."""
    return IMAPClient(host, port=int(port), ssl=True,
                      ssl_context=AccountTLS(trust),
                      timeout=timeout, use_uid=True)
