"""Encrypted local store of personal data (profiles, custom keys, learned mappings).

File format: b"S2FV1" + 12-byte nonce + AES-256-GCM(JSON), header bound as
associated data. The 32-byte key lives in the OS credential store via
`keyring` (Windows Credential Manager / DPAPI, macOS Keychain, Secret Service),
so the vault opens with the user's login and is useless on another account/PC.
Values are never logged.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import keyring
import keyring.errors
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from platformdirs import user_data_dir

MAGIC = b"S2FV1"
KEYRING_SERVICE = "Scan2Form"
KEYRING_USER = "vault-key"
SCHEMA_VERSION = 1


class VaultLockedError(Exception):
    """The vault file exists but its key is missing or wrong (other user/PC, reset credentials)."""


@dataclass
class Profile:
    name: str
    values: dict[str, str] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class CustomKey:
    key: str  # "c_<slug>", never collides with catalogue keys
    label: str


@dataclass
class Suggestion:
    slug: str  # normalized label (keys.normalize_label)
    label: str  # as seen on the form
    count: int = 1
    last_form: str = ""


@dataclass
class Vault:
    profiles: list[Profile] = field(default_factory=lambda: [Profile("Ich")])
    custom_keys: list[CustomKey] = field(default_factory=list)
    learned: dict[str, str] = field(
        default_factory=dict
    )  # normalized label -> key (or "key=choice")
    suggestions: list[Suggestion] = field(default_factory=list)
    dismissed: list[str] = field(default_factory=list)
    # Unsaved form entries per form file (absolute path -> FillSession.to_draft()).
    drafts: dict[str, dict] = field(default_factory=dict)

    def profile(self, profile_id: str) -> Profile:
        return next(p for p in self.profiles if p.id == profile_id)

    def custom_key(self, key: str) -> CustomKey | None:
        return next((c for c in self.custom_keys if c.key == key), None)

    def to_json(self) -> dict:
        return {"version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_json(cls, data: dict) -> Vault:
        if data.get("version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported vault version {data.get('version')}")
        return cls(
            profiles=[Profile(**p) for p in data["profiles"]] or [Profile("Ich")],
            custom_keys=[CustomKey(**c) for c in data["custom_keys"]],
            learned=dict(data["learned"]),
            suggestions=[Suggestion(**s) for s in data["suggestions"]],
            dismissed=list(data["dismissed"]),
            drafts=dict(data.get("drafts", {})),
        )


def default_vault_path() -> Path:
    return Path(user_data_dir("Scan2Form", appauthor=False)) / "vault.s2fv"


class VaultStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_vault_path()

    # -- key handling -------------------------------------------------------
    def _key(self, create: bool) -> bytes | None:
        stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if stored:
            try:
                key = base64.b64decode(stored, validate=True)
            except binascii.Error as exc:
                raise VaultLockedError("The stored key is damaged.") from exc
            if len(key) != 32:
                raise VaultLockedError("The stored key is damaged.")
            return key
        if not create:
            return None
        key = AESGCM.generate_key(bit_length=256)
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, base64.b64encode(key).decode())
        return key

    # -- load / save ----------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> Vault:
        """The stored vault, or a fresh one (single profile) if none exists yet."""
        if not self.path.exists():
            return Vault()
        key = self._key(create=False)
        if key is None:
            raise VaultLockedError(
                "The key for your saved data is missing from the credential store."
            )
        blob = self.path.read_bytes()
        if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 12 + 16:
            raise VaultLockedError("The data file is damaged or not a Scan2Form vault.")
        nonce, ciphertext = blob[len(MAGIC) : len(MAGIC) + 12], blob[len(MAGIC) + 12 :]
        try:
            plain = AESGCM(key).decrypt(nonce, ciphertext, MAGIC)
        except InvalidTag as exc:
            raise VaultLockedError(
                "Your saved data cannot be decrypted with this account's key."
            ) from exc
        return Vault.from_json(json.loads(plain.decode("utf-8")))

    def save(self, vault: Vault) -> None:
        key = self._key(create=True)
        nonce = secrets.token_bytes(12)
        plain = json.dumps(vault.to_json(), ensure_ascii=False).encode("utf-8")
        blob = MAGIC + nonce + AESGCM(key).encrypt(nonce, plain, MAGIC)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        if self.path.exists():
            os.replace(self.path, self.path.with_suffix(".bak"))
        os.replace(tmp, self.path)

    def reset(self) -> None:
        """Delete the vault, its backup and the key. Irreversible."""
        for path in (self.path, self.path.with_suffix(".bak"), self.path.with_suffix(".tmp")):
            path.unlink(missing_ok=True)
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except keyring.errors.PasswordDeleteError:
            pass
