from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WATCH_PATTERNS = ("src/**/*.py",)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local dev servers with code hot reload.")
    parser.add_argument(
        "mode",
        choices=["daemon", "pr-comments-daemon"],
        nargs="?",
        default="daemon",
        help="Backend daemon mode to run and restart on Python code changes.",
    )
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--no-frontend", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"

    backend = ManagedProcess(
        [
            env.get("PYTHON_BIN", "python3"),
            "-m",
            "linear_codex_orchestrator.main",
            args.mode,
            "--interval-seconds",
            str(args.interval_seconds),
        ],
        env=env,
        name="backend",
    )
    frontend = None if args.no_frontend else ManagedProcess(
        ["npm", "--prefix", "frontend", "run", "dev"],
        env=env,
        name="frontend",
    )

    processes = [process for process in (backend, frontend) if process is not None]
    shutting_down = False

    def stop_all(_signum: int | None = None, _frame: object | None = None) -> None:
        nonlocal shutting_down
        shutting_down = True
        for process in processes:
            process.stop()

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    print("Code hot reload dev mode", flush=True)
    print("Backend API: http://127.0.0.1:8765", flush=True)
    if frontend:
        print("Frontend HMR: http://127.0.0.1:5173", flush=True)
    print("Watching Python files under src/; frontend changes are handled by Vite.", flush=True)

    last_signature = source_signature()
    backend.start()
    if frontend:
        frontend.start()

    try:
        while not shutting_down:
            for process in processes:
                process.report_if_exited()
            current_signature = source_signature()
            if current_signature != last_signature:
                last_signature = current_signature
                print("Python code changed; restarting backend...", flush=True)
                backend.restart()
            time.sleep(args.poll_seconds)
    finally:
        stop_all()


def source_signature() -> tuple[tuple[str, int, int], ...]:
    files: list[tuple[str, int, int]] = []
    for pattern in WATCH_PATTERNS:
        for path in ROOT.glob(pattern):
            if "__pycache__" in path.parts:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((str(path.relative_to(ROOT)), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(files))


class ManagedProcess:
    def __init__(self, command: list[str], *, env: dict[str, str], name: str) -> None:
        self.command = command
        self.env = env
        self.name = name
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        print(f"Starting {self.name}: {' '.join(self.command)}", flush=True)
        self.process = subprocess.Popen(self.command, cwd=ROOT, env=self.env, start_new_session=True)

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        print(f"Stopping {self.name}...", flush=True)
        self._signal_process_group(signal.SIGTERM)
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self._signal_process_group(signal.SIGKILL)
            self.process.wait(timeout=8)
        finally:
            self.process = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def report_if_exited(self) -> None:
        if not self.process:
            return
        code = self.process.poll()
        if code is not None:
            print(f"{self.name} exited with code {code}", flush=True)
            self.process = None

    def _signal_process_group(self, signum: int) -> None:
        if not self.process:
            return
        try:
            os.killpg(self.process.pid, signum)
        except ProcessLookupError:
            return


if __name__ == "__main__":
    raise SystemExit(main())
