from __future__ import annotations

import argparse
import asyncio

from .config import Settings
from .orchestrator import Orchestrator

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
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
