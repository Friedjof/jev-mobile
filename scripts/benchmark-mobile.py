#!/usr/bin/env python3
"""Run durable Jev Mobile scenarios only through its public stdio MCP API.

Results deliberately live in a separate SQLite file: production task records
remain the source of truth for execution and are never annotated by a bench.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sqlite3
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def _smoke_module():
    path = Path(__file__).with_name("mcp-smoke.py")
    spec = importlib.util.spec_from_file_location("jev_mobile_mcp_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def setup(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE IF NOT EXISTS benchmark_runs(
      id INTEGER PRIMARY KEY, suite TEXT, scenario TEXT, task_id TEXT,
      started_at TEXT, finished_at TEXT, status TEXT, verified INTEGER,
      duration_ms INTEGER, steps INTEGER, jev_calls INTEGER, recoveries INTEGER,
      waiting_count INTEGER, failure_reason TEXT, result_json TEXT)""")
    return conn


async def run_one(command: list[str], instruction: str, timeout: float) -> dict[str, object]:
    smoke = _smoke_module()
    started = time.monotonic()
    async with smoke.StdioMcpClient(command) as client:
        response = await client.tool("start_task", {"instruction": instruction})
        task_id = str(response["task_id"])
    while time.monotonic() - started < timeout:
        async with smoke.StdioMcpClient(command) as client:
            task = await client.tool("get_task", {"task_id": task_id})
            events = await client.tool("get_task_events", {"task_id": task_id, "after_seq": 0})
        status = str(task["status"])
        if status in {"succeeded", "failed", "cancelled", "waiting_for_user"}:
            result = task.get("result") if isinstance(task.get("result"), dict) else {}
            event_rows = events.get("events", [])
            return {"task_id": task_id, "status": status, "duration_ms": round((time.monotonic()-started)*1000),
                    "steps": int(task.get("steps", 0)), "jev_calls": sum(1 for e in event_rows if e.get("event_type") == "ACTION_SELECTED"),
                    "recoveries": sum(1 for e in event_rows if "RECOVERY" in str(e.get("event_type"))),
                    "waiting_count": sum(1 for e in event_rows if e.get("event_type") == "TASK_WAITING_FOR_USER"),
                    "verified": bool(result.get("verified", status == "succeeded")), "failure_reason": task.get("failure"), "task": task}
        await asyncio.sleep(1)
    return {"task_id": task_id, "status": "timeout", "duration_ms": round((time.monotonic()-started)*1000),
            "steps": 0, "jev_calls": 0, "recoveries": 0, "waiting_count": 0, "verified": False, "failure_reason": "benchmark timeout", "task": {}}


async def main_async(args: argparse.Namespace) -> None:
    suite = json.loads(Path(args.suite).read_text())
    conn = setup(Path(args.results))
    command = args.command.split()
    for scenario in suite["scenarios"]:
        if args.scenario and scenario["name"] not in args.scenario:
            continue
        if not scenario.get("enabled", True):
            continue
        # An explicit override is useful for a one-scenario run; otherwise
        # retain each scenario's own repetition budget.
        for index in range(args.runs if args.runs is not None else scenario.get("runs", 1)):
            if prepare := scenario.get("prepare_command"):
                subprocess.run(str(prepare).format(run=index + 1), shell=True, check=True)
            began = datetime.now(UTC).isoformat(); row = await run_one(command, scenario["instruction"].format(run=index + 1), args.timeout)
            conn.execute("INSERT INTO benchmark_runs(suite,scenario,task_id,started_at,finished_at,status,verified,duration_ms,steps,jev_calls,recoveries,waiting_count,failure_reason,result_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (suite.get("name", "unnamed"), scenario["name"], row["task_id"], began, datetime.now(UTC).isoformat(), row["status"], int(row["verified"]), row["duration_ms"], row["steps"], row["jev_calls"], row["recoveries"], row["waiting_count"], json.dumps(row["failure_reason"], default=str), json.dumps(row["task"], default=str)))
            conn.commit(); print(json.dumps({k: row[k] for k in row if k != "task"}))
    for (name,) in conn.execute("SELECT DISTINCT scenario FROM benchmark_runs WHERE suite=?", (suite.get("name", "unnamed"),)):
        rows = conn.execute("SELECT verified,duration_ms FROM benchmark_runs WHERE suite=? AND scenario=?", (suite.get("name", "unnamed"), name)).fetchall()
        print(f"{name}: {sum(row[0] for row in rows)}/{len(rows)} verified; median {statistics.median(row[1] for row in rows)}ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="benchmarks/v1.json")
    parser.add_argument("--results", default=".benchmark/benchmark-v1.sqlite3")
    parser.add_argument("--command", required=True, help="public stdio MCP command")
    parser.add_argument("--runs", type=int)
    parser.add_argument("--scenario", action="append", help="run only this named scenario (repeatable)")
    parser.add_argument("--timeout", type=float, default=120)
    asyncio.run(main_async(parser.parse_args()))
