"""Process-local session prototype. No credentials, provider or startup defaults."""
from dataclasses import dataclass, field
import hashlib
import secrets
import threading
import time


@dataclass(frozen=True)
class Identity:
    subject: str
    organization: str
    project: str

    def __post_init__(self):
        for value in (self.subject, self.organization, self.project):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError('Invalid verified identity')


@dataclass
class Session:
    identity: Identity
    expires: float
    last_seen: float
    csrf_hash: str = field(repr=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Sessions:
    """In-memory, single-process only; restart logs everyone out."""
    def __init__(self, *, ttl=1800, idle=600, clock=time.monotonic, capacity=1000):
        if not (0 < idle <= ttl <= 86400) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError('Invalid session limits')
        self.ttl, self.idle, self.clock, self.capacity = ttl, idle, clock, capacity
        self._records = {}
        self._lock = threading.RLock()

    def issue(self, identity):
        if not isinstance(identity, Identity):
            raise ValueError('Verified Identity required')
        with self._lock:
            now = self.clock()
            self._records = {k: v for k, v in self._records.items()
                             if now < v.expires and now - v.last_seen < self.idle}
            if len(self._records) >= self.capacity:
                raise RuntimeError('Session capacity reached')
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self._records[digest(token)] = Session(identity, now + self.ttl, now, digest(csrf))
            return token, csrf

    def resolve(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            return None
        with self._lock:
            key, now = digest(token), self.clock()
            record = self._records.get(key)
            if record and (now >= record.expires or now - record.last_seen >= self.idle):
                self._records.pop(key, None)
                return None
            # Return a snapshot; callers cannot change the internal clock/expiry.
            return Session(record.identity, record.expires, record.last_seen, record.csrf_hash) if record else None

    def touch(self, token):
        with self._lock:
            if self.resolve(token):
                self._records[digest(token)].last_seen = self.clock()

    def revoke(self, token):
        if isinstance(token, str):
            with self._lock:
                self._records.pop(digest(token), None)
