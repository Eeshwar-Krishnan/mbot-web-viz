#!/usr/bin/env python3
"""Build a standalone DataLogger2.py with embedded index.html."""

from __future__ import annotations

import argparse
import base64
import gzip
from pathlib import Path

PLACEHOLDER = "__EMBEDDED_INDEX_HTML_GZ_B64__"


def build(source_py: Path, source_html: Path, output_py: Path) -> None:
    py_src = source_py.read_text(encoding="utf-8")
    html_src = source_html.read_text(encoding="utf-8")

    if PLACEHOLDER not in py_src:
        raise RuntimeError(f"Placeholder {PLACEHOLDER!r} not found in {source_py}")

    embedded = base64.b64encode(gzip.compress(html_src.encode("utf-8"), compresslevel=9)).decode("ascii")
    py_out = py_src.replace(PLACEHOLDER, embedded, 1)

    output_py.parent.mkdir(parents=True, exist_ok=True)
    output_py.write_text(py_out, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-py", type=Path, default=Path("DataLogger2.py"))
    parser.add_argument("--source-html", type=Path, default=Path("index.html"))
    parser.add_argument("--output", type=Path, default=Path("dist/DataLogger2_standalone.py"))
    args = parser.parse_args()

    build(args.source_py, args.source_html, args.output)
    print(f"Wrote standalone file: {args.output}")


if __name__ == "__main__":
    main()
