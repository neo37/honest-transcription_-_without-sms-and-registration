"""
BusinessPad MCP Server

Exposes BusinessPad platform tools via the Model Context Protocol:
  - OCR: submit a document (URL or file path) for text extraction → get Markdown
  - Org management: create organisations (requires master API key)

Environment variables:
  BUSINESSPAD_BASE_URL    Base URL of the platform (default: https://baza.business-pad.com)
  BUSINESSPAD_API_KEY     Space API key (UUID) — required for OCR tools
  BUSINESSPAD_MASTER_KEY  Master API key — required for org_create tool
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ── Config ───────────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("BUSINESSPAD_BASE_URL", "https://baza.business-pad.com").rstrip("/")
API_KEY = os.environ.get("BUSINESSPAD_API_KEY", "")
MASTER_KEY = os.environ.get("BUSINESSPAD_MASTER_KEY", "")

# ── Server instance ───────────────────────────────────────────────────────────

server = Server("businesspad")


# ── Tool definitions ──────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="ocr_submit_url",
            description=(
                "Submit a document URL (PDF, PNG, JPEG) to BusinessPad OCR queue. "
                "Returns a task_id to check later with ocr_get_status. "
                "Requires BUSINESSPAD_API_KEY env var."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Direct URL to the document (PDF/PNG/JPEG, max 50 MB)",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename hint (used to infer extension)",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="ocr_get_status",
            description=(
                "Check the status of an OCR task and retrieve the resulting Markdown text when done. "
                "Statuses: pending → processing → done | failed. "
                "Requires BUSINESSPAD_API_KEY env var."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "Task ID returned by ocr_submit_url",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="ocr_extract",
            description=(
                "Submit a document URL for OCR and wait until it completes, then return the Markdown text. "
                "Polls automatically — no need to call ocr_get_status separately. "
                "Requires BUSINESSPAD_API_KEY env var."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Direct URL to the document (PDF/PNG/JPEG, max 50 MB)",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename hint",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait for OCR to complete (default: 120)",
                        "default": 120,
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="ocr_list_done",
            description=(
                "List the most recent completed OCR jobs for this space (up to 100). "
                "Returns id, filename, created_at and extracted text for each job. "
                "Requires BUSINESSPAD_API_KEY env var."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="org_create",
            description=(
                "Create a new organisation in BusinessPad and return credentials + a magic login link. "
                "Requires BUSINESSPAD_MASTER_KEY env var (admin-only operation)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Organisation name (e.g. 'Ромашка ООО')",
                    },
                    "email": {
                        "type": "string",
                        "description": "Administrator email address",
                    },
                },
                "required": ["name", "email"],
            },
        ),
    ]


# ── Tool implementations ──────────────────────────────────────────────────────

def _api_key_headers() -> dict[str, str]:
    if not API_KEY:
        raise ValueError(
            "BUSINESSPAD_API_KEY is not set. "
            "Export it: export BUSINESSPAD_API_KEY=<your-space-uuid>"
        )
    return {"X-Api-Key": API_KEY}


def _master_key_headers() -> dict[str, str]:
    if not MASTER_KEY:
        raise ValueError(
            "BUSINESSPAD_MASTER_KEY is not set. "
            "Export it: export BUSINESSPAD_MASTER_KEY=<master-key>"
        )
    return {"X-Api-Key": MASTER_KEY}


async def _ocr_submit(url: str, filename: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"url": url}
    if filename:
        payload["filename"] = filename
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BASE_URL}/api/v1/ocr/",
            json=payload,
            headers=_api_key_headers(),
        )
        r.raise_for_status()
        return r.json()


async def _ocr_status(task_id: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BASE_URL}/api/v1/ocr/{task_id}/",
            headers=_api_key_headers(),
        )
        r.raise_for_status()
        return r.json()


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "ocr_submit_url":
            result = await _ocr_submit(
                url=arguments["url"],
                filename=arguments.get("filename"),
            )
            text = (
                f"OCR job submitted.\n"
                f"task_id: {result['task_id']}\n"
                f"status:  {result['status']}\n\n"
                f"Use ocr_get_status(task_id={result['task_id']}) to check progress."
            )
            return [TextContent(type="text", text=text)]

        elif name == "ocr_get_status":
            result = await _ocr_status(int(arguments["task_id"]))
            status = result["status"]
            if status == "done":
                text = (
                    f"task_id: {result['task_id']}  |  status: done\n"
                    f"filename: {result.get('filename', '')}\n\n"
                    f"--- Extracted text (Markdown) ---\n\n"
                    f"{result.get('markdown', '')}"
                )
            elif status == "failed":
                text = (
                    f"task_id: {result['task_id']}  |  status: failed\n"
                    f"error: {result.get('error', 'unknown error')}"
                )
            else:
                text = (
                    f"task_id: {result['task_id']}  |  status: {status}\n"
                    f"The job is still in progress. Try again in a few seconds."
                )
            return [TextContent(type="text", text=text)]

        elif name == "ocr_extract":
            timeout = int(arguments.get("timeout", 120))
            submit_result = await _ocr_submit(
                url=arguments["url"],
                filename=arguments.get("filename"),
            )
            task_id = submit_result["task_id"]

            deadline = time.monotonic() + timeout
            poll_interval = 3
            while True:
                await asyncio.sleep(poll_interval)
                result = await _ocr_status(task_id)
                status = result["status"]

                if status == "done":
                    text = (
                        f"task_id: {task_id}  |  status: done\n"
                        f"filename: {result.get('filename', '')}\n\n"
                        f"--- Extracted text (Markdown) ---\n\n"
                        f"{result.get('markdown', '')}"
                    )
                    return [TextContent(type="text", text=text)]

                if status == "failed":
                    return [TextContent(
                        type="text",
                        text=f"task_id: {task_id}  |  OCR failed: {result.get('error', 'unknown error')}",
                    )]

                if time.monotonic() > deadline:
                    return [TextContent(
                        type="text",
                        text=(
                            f"task_id: {task_id}  |  Timeout after {timeout}s (status: {status}).\n"
                            f"Use ocr_get_status(task_id={task_id}) to check later."
                        ),
                    )]

                poll_interval = min(poll_interval * 1.5, 15)

        elif name == "ocr_list_done":
            if not API_KEY:
                raise ValueError("BUSINESSPAD_API_KEY is not set.")
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"{BASE_URL}/api/space/{API_KEY}/ocr/",
                )
                r.raise_for_status()
                data = r.json()

            results = data.get("results", [])
            if not results:
                return [TextContent(type="text", text="No completed OCR jobs found.")]

            lines = [f"Space: {data.get('space', '')}  |  {len(results)} completed jobs\n"]
            for job in results:
                preview = (job.get("text") or "")[:120].replace("\n", " ")
                lines.append(
                    f"- id={job['id']}  filename={job['filename']!r}  "
                    f"created={job['created_at']}\n  preview: {preview}…"
                )
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "org_create":
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{BASE_URL}/api/v1/org/",
                    json={"name": arguments["name"], "email": arguments["email"]},
                    headers=_master_key_headers(),
                )
                r.raise_for_status()
                data = r.json()

            text = (
                f"Organisation created successfully.\n\n"
                f"Name:        {data['name']}\n"
                f"Slug:        {data['slug']}\n"
                f"Org ID:      {data['org_id']}\n"
                f"API key:     {data['api_key']}\n"
                f"Admin email: {data['email']}\n"
                f"Password:    {data['password']}\n"
                f"Magic link:  {data['magic_link']}\n\n"
                f"Send the magic link to the admin — one click and they are logged in."
            )
            return [TextContent(type="text", text=text)]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except httpx.HTTPStatusError as exc:
        return [TextContent(
            type="text",
            text=f"HTTP error {exc.response.status_code}: {exc.response.text[:500]}",
        )]
    except Exception as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    asyncio.run(_run())


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    main()
