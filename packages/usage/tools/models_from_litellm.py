"""Make usage's shipped model table, `src/moxi_usage/data/models.json`, from a copy of
LiteLLM's model price file and the checked-in supplement.

Fetch the file once, outside the repository, and note the day you fetched it:

    curl -sSfL -o /some/scratch/litellm.json \\
        https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
    uv run --locked python packages/usage/tools/models_from_litellm.py \\
        /some/scratch/litellm.json --date YYYY-MM-DD

The table records the file's sha256 and the date given. The conversion is the one
`moxi usage prices update` runs (`moxi_usage.convert`): it stops, writing nothing, when the
file lacks a field name the converter reads. It prints the rows kept by LiteLLM mode and
the table's size.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
import tomllib
from collections import Counter
from pathlib import Path

from moxi.files import atomic_write

from moxi_usage import convert

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "src" / "moxi_usage" / "data"
URL = ("https://raw.githubusercontent.com/BerriAI/litellm/main/"
       "model_prices_and_context_window.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make the shipped model table.")
    parser.add_argument("litellm", type=Path, help="a copy of LiteLLM's price file")
    parser.add_argument("--date", required=True,
                        help="the day the copy was fetched, YYYY-MM-DD")
    parser.add_argument("--url", default=URL, help="where the copy was fetched from")
    parser.add_argument("--out", type=Path, default=DATA / "models.json")
    args = parser.parse_args(argv)
    datetime.date.fromisoformat(args.date)
    raw = args.litellm.read_bytes()
    litellm = json.loads(raw)
    supplement = tomllib.loads((DATA / "supplement.toml").read_text(encoding="utf-8"))
    try:
        table = convert.convert(litellm, supplement, date=args.date,
                                sha256=hashlib.sha256(raw).hexdigest(), url=args.url)
    except convert.ConvertError as exc:
        print(f"models_from_litellm: {exc}", file=sys.stderr)
        return 1
    kept = Counter(row.get("mode") for key, row in litellm.items()
                   if isinstance(row, dict) and key in table["models"])
    atomic_write(args.out, convert.dumps(table), mode=0o644)
    print(f"{len(table['models'])} models ({', '.join(f'{m} {n}' for m, n in sorted(kept.items()))}), "
          f"{len(table['prefixes'])} prefix rows, written to {args.out}")
    print(f"snapshot {table['snapshot']['date']} sha256 {table['snapshot']['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
