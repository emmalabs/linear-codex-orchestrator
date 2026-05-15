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
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return self
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        self.acquired = True
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.acquired:
            self.lock_path.unlink(missing_ok=True)
            self.acquired = False


def lock_for_repo(lock_dir: Path, repo_full_name: str) -> RepoLock:
    safe_name = repo_full_name.replace("/", "__")
    return RepoLock(lock_dir / f"{safe_name}.lock")

