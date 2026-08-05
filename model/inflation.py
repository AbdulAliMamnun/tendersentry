"""Normalize historical contract amounts to current dollars.

A 2018 watermain contract at $1.2M is not the same job as a 2026 watermain contract at
$1.2M. The corpus spans nine years, so every amount is deflated to a common quarter
before it is used for a lookup, a training label, or a band.

**Series: StatCan table 18-10-0289-01**, "Building construction price indexes, by type
of building and division" — geography *Quebec*, type *Non-residential buildings [622]*,
division *Division composite*. Base period **2023 = 100**. Quarterly, and the Quebec
non-residential series runs **2017-Q1 to 2026-Q2** at the time of writing.

**This is a proxy, and a labelled one.** Our corpus is overwhelmingly roadwork,
watermain, sewer, and paving — *engineering* construction, not buildings. The correct
instrument would be an infrastructure or engineering construction price index, and
**Statistics Canada does not currently publish an active one**: table 18-10-0022
(Infrastructure construction price index) ends 2019, and 18-10-0096 (Highway
construction price indexes) ends 1993. Both are inactive. The choice is therefore a
live index for the wrong sector or a right-sector index that stopped seven years ago.
We use the former and say so wherever an adjusted figure surfaces.

That the right instrument does not exist is a gap in Canadian price statistics rather
than a shortcut here, which is worth recording because someone will eventually ask.

The exporter writes a compact series file so the 55 MB source CSV need not be
committed; `python3 -m model.inflation --extract <csv>` regenerates it.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import config


LOGGER = logging.getLogger(__name__)

SERIES_PATH = Path(config.PROJECT_ROOT) / "data" / "statcan" / "bcpi-quebec-nonres.json"

#: Exactly the slice we take, recorded so the artifact can cite it.
SERIES = {
    "table": "18-10-0289-01",
    "geography": "Quebec",
    "type_of_building": "Non-residential buildings [622]",
    "division": "Division composite",
    "base_period": "2023=100",
    "sector_caveat": (
        "Building index used as a proxy for engineering construction. StatCan has no "
        "active infrastructure/engineering construction price index: 18-10-0022 ends "
        "2019, 18-10-0096 ends 1993."
    ),
}

#: Amounts before the series starts cannot be adjusted, so they are excluded upstream
#: rather than silently carried at face value.
_cache: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    """The committed series: quarter (``YYYY-Qn``) to index value, plus provenance."""
    global _cache
    if _cache is None:
        _cache = json.loads(SERIES_PATH.read_text(encoding="utf-8"))
    return _cache


def quarter_of(date: str) -> str:
    """`2024-08-13` becomes `2024-Q3`."""
    year, month = int(date[:4]), int(date[5:7])
    return f"{year}-Q{(month - 1) // 3 + 1}"


def coverage() -> tuple[str, str]:
    """First and last quarter the series covers."""
    quarters = sorted(load()["index"])
    return quarters[0], quarters[-1]


def adjust(amount: float, date: str) -> float | None:
    """Restate `amount` (paid at `date`) in the latest available quarter's dollars.

    Returns None when the date falls outside the series, so a caller must decide what
    to do rather than receive an unadjusted number that looks adjusted.
    """
    index = load()["index"]
    quarter = quarter_of(date)
    if quarter not in index:
        # Past the series end is a data-lag artifact, not a gap: use the last quarter.
        last = max(index)
        if quarter > last:
            quarter = last
        else:
            return None
    factor = index[max(index)] / index[quarter]
    return float(amount) * factor


def extract(csv_path: Path | str) -> dict[str, Any]:
    """Pull our slice out of the StatCan full-table CSV and write the series file."""
    import pandas as pd

    frame = pd.read_csv(csv_path, low_memory=False)
    slice_ = frame[
        (frame["GEO"] == SERIES["geography"])
        & (frame["Type of building"] == SERIES["type_of_building"])
        & (frame["Division"] == SERIES["division"])
    ]
    if slice_.empty:
        raise RuntimeError("series slice is empty; check the dimension member names")

    index: dict[str, float] = {}
    for _, row in slice_.iterrows():
        year, month = str(row["REF_DATE"]).split("-")
        value = row["VALUE"]
        if value != value:  # NaN
            continue
        index[f"{year}-Q{(int(month) - 1) // 3 + 1}"] = float(value)

    payload = {"series": SERIES, "index": dict(sorted(index.items()))}
    SERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SERIES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    LOGGER.info(
        "Wrote %d quarters (%s to %s) to %s",
        len(index),
        min(index),
        max(index),
        SERIES_PATH,
    )
    return payload


def _main() -> None:
    parser = argparse.ArgumentParser(description="Build the price-index series file")
    parser.add_argument("--extract", help="path to the StatCan 18100289 full-table CSV")
    args = parser.parse_args()

    if args.extract:
        payload = extract(Path(args.extract))
        print(json.dumps(payload["series"], indent=2))
        print(f"quarters: {len(payload['index'])}")
    else:
        first, last = coverage()
        print(json.dumps(load()["series"], indent=2))
        print(f"coverage: {first} to {last}")
        print(f"$1.00 in 2018-Q1 is ${adjust(1.0, '2018-01-15'):.2f} today")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()
