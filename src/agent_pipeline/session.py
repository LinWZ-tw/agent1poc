"""Web-facing chat session: wraps an LLM provider around the same tool /
job / checkpoint stack `orchestrator.py` uses, but as a long-lived,
interruptible chat instead of a single run-to-completion call.

`orchestrator.run()` sends one goal and loops until the model stops calling
tools, then returns. That's fine for a CLI demo, but a GUI user needs to be
able to interject mid-run -- ask a question, correct the study design,
hand over more sample metadata -- without starting a new run. `AgentSession`
keeps one provider conversation alive in a background thread: each posted
message triggers the same tool-use loop, but the loop yields back to
"waiting_for_user" instead of exiting, so the conversation (and the
checkpoint it has been building under result/<run_id>/) just continues.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from typing import Any

from .prompts.planner import SYSTEM_PROMPT
from .providers import make_provider
from .tools import PLANNER_TOOLS

MAX_ITERATIONS_PER_TURN = 40


class AgentSession:
    def __init__(
        self,
        run_id: str,
        *,
        provider_name: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        effort: str = "high",
        auto_approve: bool = True,
    ) -> None:
        self.run_id = run_id
        self.auto_approve = auto_approve
        self.status = "idle"  # idle -> thinking -> waiting_for_user | error
        self.events: list[dict[str, Any]] = []
        self._events_lock = threading.Lock()
        self._inbox: "queue.Queue[str]" = queue.Queue()

        from .agents.planner import make_dispatch
        self._dispatch = make_dispatch(
            run_id,
            auto_approve,
            provider_name=provider_name,
            api_key=api_key,
            model=model,
            base_url=base_url,
            effort=effort,
            emit_fn=self._emit,
        )

        # Constructing the provider can fail fast (bad key format, missing
        # SDK, ...) -- let that raise synchronously so the caller can surface
        # it before a background thread ever starts.
        self.provider = make_provider(
            provider_name,
            api_key=api_key,
            model=model,
            system_prompt=SYSTEM_PROMPT,
            base_url=base_url,
            effort=effort,
            tools=PLANNER_TOOLS,
        )

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _emit(self, **event: Any) -> None:
        event["t"] = time.time()
        with self._events_lock:
            self.events.append(event)

    def events_since(self, index: int) -> tuple[list[dict[str, Any]], int]:
        with self._events_lock:
            return list(self.events[index:]), len(self.events)

    def post(self, text: str) -> None:
        """Queue a user message (initial goal or a follow-up). Non-blocking."""
        self._inbox.put(text)

    def _worker(self) -> None:
        while True:
            text = self._inbox.get()
            self.status = "thinking"
            self._emit(type="user", text=text)
            try:
                self.provider.send_user_text(text)
                self._run_until_idle()
                self.status = "waiting_for_user"
            except Exception as exc:  # noqa: BLE001 - surfaced to the GUI, thread stays alive
                tb = traceback.format_exc()
                import sys
                print(tb, file=sys.stderr)
                self._emit(type="error", text=f"{type(exc).__name__}: {exc}\n\n{tb}")
                self.status = "error"

    def _run_until_idle(self) -> None:
        for _ in range(MAX_ITERATIONS_PER_TURN):
            result = self.provider.step()
            if result.warning:
                self._emit(type="system", text=f"⚠️ {result.warning}")
            if result.thinking:
                self._emit(type="thinking", text=result.thinking)
            if result.text:
                self._emit(type="text", text=result.text)
            if result.stop_reason != "tool_use":
                return

            tool_results = []
            for call in result.tool_calls:
                self._emit(type="tool_call", name=call["name"], input=call["input"])
                try:
                    output = self._dispatch(self.run_id, call["name"], call["input"], self.auto_approve)
                    is_error = bool(isinstance(output, dict) and output.get("error"))
                except Exception as exc:  # noqa: BLE001 - surfaced back to the model, not raised
                    tb = traceback.format_exc()
                    output = {"error": f"{type(exc).__name__}: {exc}", "traceback": tb}
                    is_error = True
                self._emit(type="tool_result", name=call["name"], output=output, is_error=is_error)
                tool_results.append(
                    {"tool_use_id": call["id"], "content": json.dumps(output, default=str), "is_error": is_error}
                )
            self.provider.send_tool_results(tool_results)

        self._emit(
            type="error",
            text=f"stopped after {MAX_ITERATIONS_PER_TURN} tool-call iterations without a final reply this turn",
        )
