from __future__ import annotations

import argparse
import asyncio
import os

from .config import Settings
from .orchestrator import Orchestrator

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        env_path = ".env"
        try:
            with open(env_path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key and key not in os.environ:
                        os.environ[key] = value
        except FileNotFoundError:
            return None


async def async_main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["once", "daemon"], nargs="?", default="once")
    parser.add_argument("--interval-seconds", type=int, default=900)
    args = parser.parse_args()

    settings = Settings.from_env()
    orchestrator = Orchestrator(settings)
    try:
        if args.mode == "daemon":
            await orchestrator.run_forever(args.interval_seconds)
        else:
            await orchestrator.run_once()
    finally:
        await orchestrator.close()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
