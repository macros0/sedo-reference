"""Lock domains shared by standalone captures and catalogue writers."""
import os
from contextlib import contextmanager
from pathlib import Path


def catalog_lock_root(directory):
    path = Path(directory).resolve()
    if path.name == "fields":
        path = path.parent
    for ancestor in (path, *path.parents):
        if ancestor.name in ("state", "data", "site") and (
                ancestor == path or (ancestor.parent / "viewer-template.html").is_file()
                or (ancestor.parent / "state/current.json").is_file()):
            return ancestor.parent
    return path


@contextmanager
def file_lock(root, *, filename=".update.lock"):
    root = Path(root).resolve()
    path = root / filename
    # Closing the file or terminating the process releases the OS lock.
    with path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                if lock.tell() == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise type(exc)(exc.errno, exc.strerror, str(path)) from exc
        yield root
