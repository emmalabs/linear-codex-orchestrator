from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RepoLock:
    lock_path: Path
    acquired: bool = False

    def __enter__(self) -> "RepoLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = self._try_open()
        if fd is None:
            return self
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        self.acquired = True
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.acquired:
            self.lock_path.unlink(missing_ok=True)
            self.acquired = False

    def _try_open(self) -> int | None:
        for _attempt in range(2):
            try:
                return os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if not lock_file_is_stale(self.lock_path):
                    return None
                self.lock_path.unlink(missing_ok=True)
        return None


def lock_for_repo(lock_dir: Path, repo_full_name: str) -> RepoLock:
    safe_name = repo_full_name.replace("/", "__")
    return RepoLock(lock_dir / f"{safe_name}.lock")


def lock_file_is_stale(lock_path: Path) -> bool:
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False
