#!/usr/bin/env python3
"""Local web server bridging the GUI (static/index.html) to the agent's
chat-session / tool / job / checkpoint stack.

Stdlib HTTP server on purpose -- no Flask/FastAPI dependency added just for
this. The vendor SDKs (`anthropic`, `openai`) are imported lazily inside
`providers.py`, only when a session actually picks that provider.

API keys are received in a POST body from the browser and forwarded
straight to the chosen LLM's SDK client in-process; this server never logs
or persists them to disk.

Run:
    python server.py                  # http://127.0.0.1:8000/
    python server.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent_pipeline import RESULT_DIR, state  # noqa: E402
from agent_pipeline.session import AgentSession  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSIONS: dict[str, AgentSession] = {}

DEFAULT_GOAL = (
    "Inspect the given data source, determine whether it is WES (exome) or scRNA data, "
    "and run the appropriate branch of the pipeline (QC/alignment/mutation-calling for WES, "
    "or cell-annotation/clustering/differential-expression/GSEA for scRNA), discovering any "
    "additional relevant existing data along the way."
)


def _build_initial_message(run_id: str, *, data_path: str, goal: str, study_design: str, sample_notes: str) -> str:
    parts = [f"Goal: {goal}", f"Primary data path to start from: {data_path}"]
    if study_design.strip():
        parts.append(f"Study design / cohort context provided by the user:\n{study_design.strip()}")
    if sample_notes.strip():
        parts.append(f"Additional sample information provided by the user:\n{sample_notes.strip()}")
    parts.append(f"Checkpoint state for run '{run_id}' so far:\n{state.summarize_state(run_id)}")
    parts.append("Begin by inspecting the data, then proceed through the appropriate branch.")
    return "\n\n".join(parts)


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentPipelineGUI/1.0"

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw) if raw else {}

    def _serve_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            return self._serve_static("index.html", "text/html")

        # --- events poll ---
        m = re.match(r"^/api/sessions/([^/]+)/events$", path)
        if m:
            session = SESSIONS.get(m.group(1))
            if session is None:
                return self._send_json({"error": "unknown run_id"}, 404)
            since = int(parse_qs(parsed.query).get("since", ["0"])[0])
            events, total = session.events_since(since)
            return self._send_json({"events": events, "next": total, "status": session.status})

        # --- checkpoint state ---
        m = re.match(r"^/api/sessions/([^/]+)/state$", path)
        if m:
            return self._send_json(state.load_state(m.group(1)))

        # --- report ready check ---
        m = re.match(r"^/api/sessions/([^/]+)/report_ready$", path)
        if m:
            run_id = m.group(1)
            rpath = RESULT_DIR / run_id / "report" / "report.html"
            ready = rpath.exists()
            return self._send_json({
                "ready": ready,
                "url": f"/result/{run_id}/report/report.html" if ready else None,
            })

        # --- serve report files (HTML, Markdown, PNG figures) ---
        m = re.match(r"^/result/([^/]+)/report/(.+)$", path)
        if m:
            run_id, sub = m.group(1), m.group(2)
            # block path traversal
            if ".." in sub or sub.startswith("/"):
                return self._send_json({"error": "invalid path"}, 400)
            fpath = RESULT_DIR / run_id / "report" / sub
            if not fpath.exists() or not fpath.is_file():
                return self._send_json({"error": "not found"}, 404)
            ct, _ = mimetypes.guess_type(str(fpath))
            return self._serve_bytes(fpath.read_bytes(), ct or "application/octet-stream")

        if path == "/api/health":
            return self._send_json({"ok": True})

        # --- check whether a file exists (used by the "Use demo data" button) ---
        if path == "/api/file_exists":
            raw = parse_qs(parsed.query).get("path", [""])[0]
            # restrict to repo-relative paths only (no absolute or traversal)
            if not raw or raw.startswith("/") or ".." in raw:
                return self._send_json({"error": "invalid path"}, 400)
            fpath = Path(__file__).resolve().parent / raw
            return self._send_json({"exists": fpath.exists()})

        return self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/sessions":
            body = self._read_json()
            run_id = body.get("run_id") or f"web-{uuid.uuid4().hex[:8]}"

            # --- reconnect: session still alive in this process ---
            if run_id in SESSIONS:
                session = SESSIONS[run_id]
                return self._send_json({
                    "run_id": run_id,
                    "status": session.status,
                    "resumed": True,
                    "event_count": len(session.events),
                })

            provider_name = body.get("provider", "anthropic")
            default_model = {
                "anthropic": "claude-opus-4-8",
                "gemini":    "gemini-2.5-flash",
                "grok":      "grok-3",
            }.get(provider_name, "gpt-4o")
            try:
                session = AgentSession(
                    run_id,
                    provider_name=provider_name,
                    api_key=body.get("api_key", ""),
                    model=body.get("model") or default_model,
                    base_url=body.get("base_url") or None,
                    effort=body.get("effort", "high"),
                    auto_approve=bool(body.get("auto_approve", True)),
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the GUI
                return self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)

            SESSIONS[run_id] = session

            # --- resume from on-disk checkpoint (post-restart) ---
            checkpoint_exists = (RESULT_DIR / run_id / "state.json").exists()
            if checkpoint_exists and not body.get("data_path", "").strip():
                initial = (
                    f"Resume run '{run_id}' from the existing checkpoint. "
                    f"Call read_checkpoint to see what has already been completed, "
                    f"then continue from where the previous session left off."
                )
            else:
                initial = _build_initial_message(
                    run_id,
                    data_path=body.get("data_path", ""),
                    goal=body.get("goal") or DEFAULT_GOAL,
                    study_design=body.get("study_design", ""),
                    sample_notes=body.get("sample_notes", ""),
                )
            session.post(initial)
            return self._send_json({
                "run_id": run_id,
                "status": session.status,
                "resumed": checkpoint_exists and not body.get("data_path", "").strip(),
            })

        m = re.match(r"^/api/sessions/([^/]+)/message$", path)
        if m:
            session = SESSIONS.get(m.group(1))
            if session is None:
                return self._send_json({"error": "unknown run_id"}, 404)
            text = self._read_json().get("text", "")
            if not text.strip():
                return self._send_json({"error": "empty message"}, 400)
            session.post(text)
            return self._send_json({"status": "queued"})

        return self._send_json({"error": "not found"}, 404)

    def _serve_static(self, name: str, content_type: str) -> None:
        fpath = STATIC_DIR / name
        if not fpath.exists():
            return self._send_json({"error": f"missing static file: {name}"}, 404)
        body = fpath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"=== agent pipeline GUI on http://{args.host}:{args.port}/ (Ctrl+C to stop) ===")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
