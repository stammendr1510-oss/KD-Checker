"""One parser per trusted local repository; worker owns the kernel file lock.

Never unlink the gate: doing so would create independently lockable inodes.
This is capacity admission, not an OS sandbox or a distributed lock.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat


class CapacityUnavailable(Exception):
    pass


def _open(path, *, create=False):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise CapacityUnavailable()
    flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    if create:
        flags |= os.O_CREAT
    descriptor = None
    try:
        descriptor = os.open(path, flags, 0o600)
        info = os.fstat(descriptor)
        name = path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0
                or (info.st_dev, info.st_ino) != (name.st_dev, name.st_ino)
                or stat.S_ISLNK(name.st_mode)):
            raise CapacityUnavailable()
        if os.name == 'posix' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise CapacityUnavailable()
        # No subprocess inherits this fd; the parser itself opens and owns it.
        os.set_inheritable(descriptor, False)
        return descriptor
    except (OSError, CapacityUnavailable):
        if descriptor is not None:
            os.close(descriptor)
        raise CapacityUnavailable() from None


def prepare(repository_path):
    """Trusted startup only, never a request-selected path or network share."""
    repository = Path(repository_path).resolve(strict=True)
    path = repository.with_name(repository.name + '.parser-capacity')
    descriptor = _open(path, create=True)
    os.close(descriptor)
    return path


@contextmanager
def claim(path):
    descriptor = _open(path)
    locked = False
    try:
        try:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif os.name == 'nt':
                import msvcrt
                # Windows permits byte-range locking beyond EOF; no file writes.
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                raise CapacityUnavailable()
            locked = True
        except OSError:
            raise CapacityUnavailable() from None
        yield
    finally:
        try:
            if locked:
                if os.name == 'posix':
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                elif os.name == 'nt':
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)
