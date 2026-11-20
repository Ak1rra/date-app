import hashlib
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """sha256 hex digest; only the hash of a token is ever stored."""
    return hashlib.sha256(token.encode()).hexdigest()
