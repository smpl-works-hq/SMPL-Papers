"""Measure matched linear and indexed lookups on a small deterministic workload."""

import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter_ns


def timed(lookup, queries):
    start = perf_counter_ns()
    checksum = sum(lookup(key) for key in queries)
    return perf_counter_ns() - start, checksum


def measure(size):
    rows = [(i, 3 * i) for i in range(size)]
    index = dict(rows)
    queries = [(i * 997) % size for i in range(200)]

    def scan(key):
        return next(value for row_key, value in rows if row_key == key)

    samples = {"scan": [], "index": []}
    expected = sum(3 * key for key in queries)
    for repeat in range(6):
        methods = [("scan", scan), ("index", index.__getitem__)]
        for name, lookup in (methods if repeat % 2 == 0 else reversed(methods)):
            elapsed, checksum = timed(lookup, queries)
            if checksum != expected:
                raise ValueError("Lookup returned unexpected values.")
            samples[name].append(elapsed)
    return {"rows": size, "queries": len(queries), "unit": "ns per batch", "samples": samples,
            "speedup": median(samples["scan"]) / median(samples["index"])}


if __name__ == "__main__":
    batches = [measure(n) for n in (256, 1024, 4096)]
    Path(os.environ["SMPL_METRICS"]).write_text(
        json.dumps({"speedup": batches[-1]["speedup"], "batches": batches}, indent=2), encoding="utf-8")
