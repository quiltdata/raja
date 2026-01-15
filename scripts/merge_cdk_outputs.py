#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: merge_cdk_outputs.py <input...> <output>", file=sys.stderr)
        return 2

    *inputs, output = sys.argv[1:]
    merged: dict[str, object] = {}

    for name in inputs:
        path = Path(name)
        if not path.is_file():
            continue
        payload = json.loads(path.read_text())
        if isinstance(payload, dict):
            merged.update(payload)

    if not merged:
        return 0

    out_path = Path(output)
    out_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
