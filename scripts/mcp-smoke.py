#!/usr/bin/env python3
"""Exercise the public Jev Mobile MCP server through real stdio JSON-RPC.

The harness deliberately knows only the six public tools.  It can launch the
Docker Compose MCP service by default, start a task, disconnect, then reconnect
and inspect the same durable task id.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
from collections.abc import Sequence


PUBLIC_TOOLS = {
    "start_task", "get_task", "get_task_events", "cancel_task", "answer_task", "get_device_status",
}


class StdioMcpClient:
    def __init__(self, command: Sequence[str]) -> None:
        self.command = command
        self.process: asyncio.subprocess.Process | None = None
        self.next_id = 1

    async def __aenter__(self) -> "StdioMcpClient":
        self.process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self.request("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "jev-mobile-mcp-smoke", "version": "1"},
        })
        await self.notify("notifications/initialized", {})
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.process and self.process.stdin:
            self.process.stdin.close()
            await self.process.stdin.wait_closed()
        if self.process:
            await self.process.wait()

    async def notify(self, method: str, params: dict[str, object]) -> None:
        assert self.process and self.process.stdin
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        assert self.process and self.process.stdin and self.process.stdout
        request_id = self.next_id
        self.next_id += 1
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n").encode())
        await self.process.stdin.drain()
        while line := await self.process.stdout.readline():
            message = json.loads(line)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"MCP {method} failed: {message['error']}")
            return message["result"]
        raise RuntimeError(f"MCP server closed while waiting for {method}")

    async def tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        content = result.get("content", [])
        if not content:
            return {}
        return json.loads(content[0]["text"])


async def smoke(command: Sequence[str], instruction: str | None, task_id: str | None,
                question_id: str | None, answer: str | None, cancel: bool) -> None:
    async with StdioMcpClient(command) as client:
        listed = await client.request("tools/list", {})
        names = {tool["name"] for tool in listed["tools"]}
        if names != PUBLIC_TOOLS:
            raise RuntimeError(f"unexpected public MCP tools: {sorted(names)}")
        print("tools:", ", ".join(sorted(names)))
        device = await client.tool("get_device_status", {})
        print("device:", "connected" if device.get("connected") else "unavailable")
        if instruction:
            started = await client.tool("start_task", {"instruction": instruction})
            task_id = str(started["task_id"])
            print("started:", task_id, started["status"])
    if not task_id:
        return
    async with StdioMcpClient(command) as client:
        task = await client.tool("get_task", {"task_id": task_id})
        events = await client.tool("get_task_events", {"task_id": task_id, "after_seq": 0})
        print("reconnected:", task_id, task["status"], "events:", len(events["events"]))
        after_seq = int(events["next_after_seq"])
        incremental = await client.tool("get_task_events", {"task_id": task_id, "after_seq": after_seq})
        if incremental["events"]:
            raise RuntimeError("incremental event cursor returned already consumed events")
        if answer:
            if not question_id:
                question = task.get("waiting_for_user")
                if not isinstance(question, dict) or not isinstance(question.get("id"), str):
                    raise RuntimeError("--answer needs a waiting task or --question-id")
                question_id = question["id"]
            queued = await client.tool("answer_task", {
                "task_id": task_id, "question_id": question_id, "answer": answer,
            })
            print("answered:", queued["status"])
        if cancel:
            cancelled = await client.tool("cancel_task", {"task_id": task_id})
            print("cancel_requested:", cancelled["cancellation_requested"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", default="docker compose run --rm -T mcp", help="stdio MCP command")
    parser.add_argument("--instruction")
    parser.add_argument("--task-id")
    parser.add_argument("--question-id")
    parser.add_argument("--answer")
    parser.add_argument("--cancel", action="store_true")
    args = parser.parse_args()
    asyncio.run(smoke(shlex.split(args.command), args.instruction, args.task_id, args.question_id, args.answer, args.cancel))


if __name__ == "__main__":
    main()
