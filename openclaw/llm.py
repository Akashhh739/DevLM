"""OpenAI agent loop on the Responses API.

Kept provider-thin: call the model, execute any function calls via `dispatch`,
feed the outputs back (threaded with `previous_response_id`), and return the final
text. The Responses API is required for function tools on the gpt-6 reasoning models.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger()

Dispatch = Callable[[str, dict[str, Any]], Any]


def run_tool_loop(
    client: Any,
    model: str,
    instructions: str,
    user_input: str,
    tools: list[dict[str, Any]],
    dispatch: Dispatch,
    max_iters: int = 32,
) -> str:
    """Drive the agent loop until the model returns a final (tool-free) answer."""
    input_items: list[dict[str, Any]] = [{"role": "user", "content": user_input}]
    prev_id: str | None = None

    for i in range(max_iters):
        resp = client.responses.create(
            model=model,
            instructions=instructions,
            input=input_items,
            tools=tools,
            previous_response_id=prev_id,
        )
        prev_id = resp.id

        calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
        if not calls:
            return resp.output_text or ""

        # Only the new function_call_output items are resent; prior turn is threaded
        # server-side via previous_response_id.
        input_items = []
        for call in calls:
            try:
                args = json.loads(call.arguments or "{}")
                result = dispatch(call.name, args)
            except Exception as exc:  # noqa: BLE001 - any tool failure is fed back, not raised
                result = f"ERROR: {exc}"
            # The decision trail: which tool, with what args, and a preview of the result.
            log.info(
                "tool_call",
                iteration=i,
                name=call.name,
                args=_preview(args, 200),
                result=_preview(result, 200),
            )
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": _stringify(result)[:20_000],
                }
            )

    return "Reached the tool-call limit without a final diagnosis."


def _stringify(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str, indent=2)


def _preview(value: Any, limit: int) -> str:
    """Short one-line preview of args/results for the decision log."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"
