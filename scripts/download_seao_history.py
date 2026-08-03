"""Download SEAO's full weekly OCDS history, politely and resumably.

Roughly 300 weekly files at ~16 MB each. The job is designed to be interrupted: every
file is cached under ``data/seao/`` by name, and a re-run skips what is already there,
so a crash costs at most the file in flight.

Politeness toward Données Québec follows the census: an honest User-Agent and a
deliberate pause between requests. This is a bulk download of an open dataset, not a
crawl, but the pause costs us an hour we were going to spend waiting anyway.
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import config
from notices import seao


LOGGER = logging.getLogger(__name__)

#: Seconds between file requests. The census uses five per host; this is one host.
PAUSE_SECONDS = 3.0


def download_all(
    cache_dir: Path | str | None = None,
    pause: float = PAUSE_SECONDS,
    limit: int | None = None,
) -> dict:
    """Fetch every weekly file not already cached. Returns a tally."""
    directory = Path(cache_dir) if cache_dir else Path(config.SEAO_CACHE_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    resources = seao.discover_weekly_resources()
    LOGGER.info("Discovered %d weekly resources", len(resources))

    present = {path.name for path in directory.glob("hebdo_*.json") if path.stat().st_size > 0}
    pending = [item for item in resources if item["name"] not in present]
    if limit:
        pending = pending[: int(limit)]
    LOGGER.info(
        "Already cached: %d | to download: %d (~%.1f GB at 16 MB each)",
        len(present),
        len(pending),
        len(pending) * 16 / 1024,
    )

    tally = {"cached": len(present), "downloaded": 0, "failed": 0}
    for index, resource in enumerate(pending, start=1):
        try:
            seao.fetch_weekly_file(resource, directory)
            tally["downloaded"] += 1
        except RuntimeError as exc:
            LOGGER.error("Failed %s: %s", resource["name"], exc)
            tally["failed"] += 1
        if index % 10 == 0:
            LOGGER.info(
                "Progress: %d of %d downloaded, %d failed",
                tally["downloaded"],
                len(pending),
                tally["failed"],
            )
        if index < len(pending):
            time.sleep(pause)

    final = sorted(directory.glob("hebdo_*.json"))
    total_bytes = sum(path.stat().st_size for path in final)
    tally["files_on_disk"] = len(final)
    tally["gigabytes"] = round(total_bytes / 1_073_741_824, 2)
    tally["expected"] = len(resources)
    tally["complete"] = len(final) >= len(resources)
    LOGGER.info(
        "Done: %d files on disk (%.2f GB) of %d expected",
        len(final),
        tally["gigabytes"],
        len(resources),
    )
    return tally


def _main() -> None:
    parser = argparse.ArgumentParser(description="Download SEAO's weekly history")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    tally = download_all(args.cache_dir, args.pause, args.limit)
    for key, value in tally.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
