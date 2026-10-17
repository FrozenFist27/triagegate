#!/usr/bin/env python
"""Dump per-node results of the latest n8n executions (flatted decode) from a copied n8n sqlite.

  docker cp <n8n-container>:/home/node/.n8n/database.sqlite* <dir>   (db + -wal + -shm)
  python scripts/inspect_n8n_exec.py <dir>/database.sqlite [execution_id]
"""
import json
import sqlite3
import sys


def unflatten(arr):
    cache = {}

    def res(i):
        if i in cache:
            return cache[i]
        v = arr[i]
        if isinstance(v, list):
            out = []
            cache[i] = out
            out.extend(res(int(x)) if isinstance(x, str) and x.isdigit() else x for x in v)
            return out
        if isinstance(v, dict):
            out = {}
            cache[i] = out
            for k, x in v.items():
                out[k] = res(int(x)) if isinstance(x, str) and x.isdigit() else x
            return out
        return v

    return res(0)


def main():
    c = sqlite3.connect(sys.argv[1])
    ids = [int(sys.argv[2])] if len(sys.argv) > 2 else [r[0] for r in c.execute("select id from execution_entity order by id desc limit 1")]
    for i in ids:
        d = json.loads(c.execute("select data from execution_data where executionId=?", (i,)).fetchone()[0])
        run = unflatten(d)["resultData"]["runData"]
        for node, runs in run.items():
            for r in runs:
                items = (r.get("data", {}).get("main") or [[]])
                short = json.dumps(items)[:260]
                print(f"[{i}] {node}: {r.get('executionStatus')} {short}")


if __name__ == "__main__":
    main()
