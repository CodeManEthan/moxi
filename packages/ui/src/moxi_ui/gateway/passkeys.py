"""Passkeys: the creation and request options, their checks with `webauthn`, and the
signed challenge a sign-in uses.

`webauthn` is the `gateway` extra's, so it is imported only inside the functions that use
it; importing this module needs nothing beyond the standard library.

A sign-in's begin stores nothing. Its challenge is signed: 16 random bytes, an expiry 60 s
ahead and an HMAC-SHA-256 tag over both, under a key the shell makes at start and keeps only
in memory, so only this running shell's challenges pass. A finish whose assertion verifies
then spends its challenge, under one lock, in a set kept until the challenge expires; a
challenge already in the set is refused. Nothing but a verified finish adds to that set, so
a caller without a passkey can't fill it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["CHALLENGE_TTL", "CREATE_TIMEOUT_MS", "Asserted", "Challenges", "Refused",
           "Registered", "assertion_parts", "b64url", "creation_options", "from_b64url",
           "request_options", "verify_assertion", "verify_creation"]

#: Seconds a sign-in's challenge lives.
CHALLENGE_TTL = 60
#: The creation options' `timeout`, the pairing state's life.
CREATE_TIMEOUT_MS = 120_000
#: The name the passkey prompt shows for the site.
RP_NAME = "Moxi"

_RANDOM = 16
_EXPIRY = 8
_TAG = 32
_CHALLENGE_SIZE = _RANDOM + _EXPIRY + _TAG


class Refused(Exception):
    """A credential or challenge that doesn't pass; `code` is the audit code,
    `credential` or `challenge`."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_b64url(text: object) -> bytes:
    """The bytes of base64url `text`, padded or not. Raises `ValueError`."""
    if not isinstance(text, str) or not text.isascii():
        raise ValueError("not base64url text")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as err:
        raise ValueError("not base64url text") from err


# --- the signed challenge -------------------------------------------------------------


class Challenges:
    """Sign-in challenges: minted signed, checked by their tag and expiry, spent once."""

    def __init__(self, *, ttl: float = CHALLENGE_TTL, clock: Callable[[], float] = time.time):
        self.ttl = ttl
        self.clock = clock
        self._key = secrets.token_bytes(32)
        self._spent: dict[bytes, float] = {}
        self._lock = threading.Lock()

    def _tag(self, head: bytes) -> bytes:
        return hmac.new(self._key, head, hashlib.sha256).digest()

    def mint(self) -> bytes:
        """A new challenge. Stores nothing."""
        expires = int(self.clock() + self.ttl)
        head = secrets.token_bytes(_RANDOM) + expires.to_bytes(_EXPIRY, "big")
        return head + self._tag(head)

    def expiry(self, challenge: bytes) -> float | None:
        """The challenge's expiry when its tag is this shell's and it hasn't expired."""
        if not isinstance(challenge, bytes) or len(challenge) != _CHALLENGE_SIZE:
            return None
        head, tag = challenge[:-_TAG], challenge[-_TAG:]
        if not hmac.compare_digest(tag, self._tag(head)):
            return None
        expires = int.from_bytes(head[_RANDOM:], "big")
        return float(expires) if self.clock() < expires else None

    def spend(self, challenge: bytes, expires: float) -> bool:
        """Add a verified finish's challenge to the spent set; False when it was there."""
        now = self.clock()
        with self._lock:
            for old in [c for c, until in self._spent.items() if until <= now]:
                del self._spent[old]
            if challenge in self._spent:
                return False
            self._spent[challenge] = expires
            return True

    def spent(self) -> int:
        """How many challenges the spent set holds now."""
        with self._lock:
            return len(self._spent)


# --- the options ----------------------------------------------------------------------


def creation_options(*, rp_id: str, user_id: bytes, name: str, challenge: bytes) -> dict:
    """The options `navigator.credentials.create` takes, as JSON-able data: a discoverable
    passkey, user verification required, no attestation, 120 s."""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (AttestationConveyancePreference,
                                          AuthenticatorSelectionCriteria,
                                          ResidentKeyRequirement, UserVerificationRequirement)

    options = generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME, user_id=user_id, user_name=name, user_display_name=name,
        challenge=challenge, timeout=CREATE_TIMEOUT_MS,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED))
    return json.loads(options_to_json(options))


def request_options(*, rp_id: str, challenge: bytes) -> dict:
    """The options `navigator.credentials.get` takes: any passkey of `rp_id` (an empty
    `allowCredentials`), user verification required."""
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement

    options = generate_authentication_options(
        rp_id=rp_id, challenge=challenge, timeout=CHALLENGE_TTL * 1000, allow_credentials=[],
        user_verification=UserVerificationRequirement.REQUIRED)
    return json.loads(options_to_json(options))


# --- the checks -----------------------------------------------------------------------


@dataclass(frozen=True)
class Registered:
    """A verified new passkey: its credential id and public key, base64url, and its count."""

    credential: str
    public_key: str
    sign_count: int


@dataclass(frozen=True)
class Asserted:
    """A verified sign-in: the authenticator's new sign count."""

    sign_count: int


def verify_creation(credential: Mapping[str, Any], *, challenge: bytes, origin: str,
                    rp_id: str) -> Registered:
    """Check a `navigator.credentials.create` result against the pairing's challenge, the
    gateway's origin and `rp_id`, with the user verified. Raises `Refused("credential")`."""
    from webauthn import verify_registration_response

    try:
        verified = verify_registration_response(
            credential=dict(credential), expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, require_user_verification=True)
    except Exception as err:  # noqa: BLE001 - any failure, malformed input included, refuses
        raise Refused("credential", f"the new passkey doesn't verify: {err}") from None
    return Registered(b64url(verified.credential_id), b64url(verified.credential_public_key),
                      verified.sign_count)


def assertion_parts(credential: object) -> tuple[str, bytes]:
    """The credential id (base64url, as the device file keeps it) and the challenge an
    assertion names, read before anything is verified. Raises `Refused("credential")`."""
    try:
        if not isinstance(credential, Mapping):
            raise ValueError("not an object")
        raw_id = b64url(from_b64url(credential["rawId"]))
        response = credential["response"]
        client = json.loads(from_b64url(response["clientDataJSON"]))
        challenge = from_b64url(client["challenge"])
    except (KeyError, TypeError, ValueError) as err:
        raise Refused("credential", f"the assertion can't be read: {err}") from None
    return raw_id, challenge


def verify_assertion(credential: Mapping[str, Any], *, challenge: bytes, origin: str,
                     rp_id: str, public_key: str, sign_count: int) -> Asserted:
    """Check a `navigator.credentials.get` result: the challenge, the origin, `rp_id`, the
    signature under `public_key`, the user verified, and a sign count that moves forward
    unless both counts are 0. Raises `Refused("credential")`."""
    from webauthn import verify_authentication_response

    try:
        verified = verify_authentication_response(
            credential=dict(credential), expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, credential_public_key=from_b64url(public_key),
            credential_current_sign_count=sign_count, require_user_verification=True)
    except Exception as err:  # noqa: BLE001 - any failure, malformed input included, refuses
        raise Refused("credential", f"the assertion doesn't verify: {err}") from None
    return Asserted(verified.new_sign_count)
