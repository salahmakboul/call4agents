# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Print the Python kit's canonical text and effect key for each vector in vectors.json (one JSON line each).

    PYTHONPATH=<repo> python js/test/fixtures/python_keys.py js/test/fixtures/vectors.json
{"$bigint": "..."} stands for a whole number that JSON cannot carry exactly (an int here, a BigInt in JS)."""
import json
import sys

from call4agents.ledger import canonical, effect_key


def value(v):
    if isinstance(v, dict):
        if set(v) == {"$bigint"}:
            return int(v["$bigint"])
        return {k: value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [value(x) for x in v]
    return v


def main(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for group in ("agree", "differ"):
        for v in data[group]:
            args = value(v)
            print(json.dumps({"group": group, "canonical": canonical(args), "key": effect_key(data["run"], data["tool"], args)}))


if __name__ == "__main__":
    main(sys.argv[1])
