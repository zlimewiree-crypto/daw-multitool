"""Store large OAuth records in Windows Credential Manager without exceeding its blob limit.

CredentialStore holds its file lock for reads and writes. Write new chunks before
switching the manifest, then remove the previous chunks; a failed save keeps the old login.
"""
import base64
import json
import logging
import uuid

PREFIX = "daw-multitool-chunks:"
CHUNK_SIZE = 1000  # ASCII chars: 2000 UTF-16 bytes, below Windows' 2560-byte limit


class WindowsVault:
    def __init__(self, backend=None):
        if backend is None:
            from keyring.backends.Windows import WinVaultKeyring
            backend = WinVaultKeyring()
        self.backend = backend

    def _manifest(self, value):
        return json.loads(value[len(PREFIX):]) if value and value.startswith(PREFIX) else None

    @staticmethod
    def _service(service, manifest, index):
        return f"{service}.{manifest['id']}.{index}"

    def get_password(self, service, username):
        value = self.backend.get_password(service, username)
        manifest = self._manifest(value)
        if manifest is None:
            return value
        pieces = [self.backend.get_password(self._service(service, manifest, i), username)
                  for i in range(manifest["count"])]
        if any(piece is None for piece in pieces):
            raise OSError("Saved account data is incomplete. Reconnect the account.")
        return base64.b64decode("".join(pieces), validate=True).decode("utf-8")

    def _remove(self, service, username, manifest):
        if manifest is None:
            return
        for i in range(manifest["count"]):
            try:
                self.backend.delete_password(self._service(service, manifest, i), username)
            except Exception:
                logging.warning("Windows could not remove an obsolete encrypted credential chunk.")

    def set_password(self, service, username, password):
        previous = self._manifest(self.backend.get_password(service, username))
        text = base64.b64encode(password.encode("utf-8")).decode("ascii")
        pieces = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
        manifest = {"id": uuid.uuid4().hex, "count": len(pieces)}
        written = 0
        try:
            for i, piece in enumerate(pieces):
                self.backend.set_password(self._service(service, manifest, i), username, piece)
                written += 1
            self.backend.set_password(service, username, PREFIX + json.dumps(manifest))
        except Exception:
            self._remove(service, username, {**manifest, "count": written})
            raise
        self._remove(service, username, previous)

    def delete_password(self, service, username):
        previous = self._manifest(self.backend.get_password(service, username))
        self.backend.delete_password(service, username)
        self._remove(service, username, previous)
