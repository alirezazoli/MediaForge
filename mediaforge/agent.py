"""The built-in assistant: a local model with real access to the app.

It is not a chatbot bolted on the side. It calls the same functions the buttons call, so
"make me a 20 second vertical clip from that folder, rainy mood" ends with a rendered
file rather than instructions.

Two rules shape the design:

  * Anything that spends money or writes a file is marked `confirm`, and the UI asks
    first. A local model hallucinating a render is cheap; hallucinating an API call is not.
  * Every tool call and its result is surfaced to the user. A hidden agent is impossible
    to trust or debug.
"""

from __future__ import annotations

import json
import sqlite3
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import app_data_dir

MAX_ROUNDS = 8               # tool calls per user turn before we stop and report
MAX_RESULT_CHARS = 4000      # keep tool output from swamping a small model's context

SYSTEM_PROMPT = """You are the assistant inside MediaForge Studio, a desktop app for \
turning photos into videos and editing media. You are running locally on the user's machine.

You have tools that control the real application. Use them rather than describing steps.
Prefer one tool call at a time, look at the result, then decide the next step.

Rules:
- Never claim you did something unless a tool result confirms it.
- If a tool fails, say so plainly and suggest the fix. Do not invent success.
- Tools marked as needing confirmation will pause for the user's approval; that is normal.
- Keep answers short. The user can see the tool results, so do not repeat them at length.
- If the user asks for something the app cannot do, say so instead of improvising.

Vocabulary you can use in tool arguments:
- moves: Zoom in, Zoom out, Pan left, Pan right, Pan up, Pan down, Zoom in + pan up, \
Still (no motion), Auto
- atmospheres: None, Clean cinematic, Warm bloom, City night glow, Golden haze, \
Teal and orange, Cool blue night, Faded film, High contrast mono, Rain, Snow, Fog drift
- transforms: Auto, Camera move (free), AI video, Still
"""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    confirm: bool = False
    danger_note: str = ""

    def schema(self) -> dict:
        """Emits strict JSON Schema.

        `required` belongs on the parent object as a list of names. Leaving a boolean
        `required` inside a property makes Ollama reject the whole request with
        "cannot unmarshal bool", so the flag is lifted out and stripped here.
        """
        properties: dict[str, Any] = {}
        required: list[str] = []
        for key, spec in self.parameters.items():
            cleaned = {name: value for name, value in spec.items() if name != "required"}
            cleaned.setdefault("type", "string")
            properties[key] = cleaned
            if spec.get("required"):
                required.append(key)

        parameters: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


FAKE_CALL_MARKERS = (
    "<tool>", "<tool_call>", "</tool>", "<function", "```tool", "[tool_call",
    "tool has returned", "tool returned:", "calling the tool",
)


def _looks_like_a_faked_call(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in FAKE_CALL_MARKERS)


@dataclass
class AgentEvent:
    kind: str                 # status | tool_call | tool_result | answer | error | confirm
    text: str = ""
    tool: str = ""
    arguments: dict = field(default_factory=dict)
    payload: Any = None


# ======================================================================================
# Persistent conversation
# ======================================================================================

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    project    TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    extra      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_project ON messages(project, id);
"""


class AgentMemory:
    """Conversation history that survives restarts, kept per project."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / "agent.db")
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(MEMORY_SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass

    def add(self, project: str, role: str, content: str, extra: dict | None = None) -> None:
        self.connection.execute(
            "INSERT INTO messages(created_at, project, role, content, extra) VALUES(?,?,?,?,?)",
            (time.time(), project, role, content, json.dumps(extra or {}, default=str)),
        )
        self.connection.commit()

    def history(self, project: str, limit: int = 40) -> list[dict]:
        rows = self.connection.execute(
            "SELECT role, content FROM messages WHERE project = ? ORDER BY id DESC LIMIT ?",
            (project, limit),
        ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def transcript(self, project: str, limit: int = 500) -> list[dict]:
        rows = self.connection.execute(
            "SELECT created_at, role, content, extra FROM messages WHERE project = ? "
            "ORDER BY id DESC LIMIT ?", (project, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def clear(self, project: str = "") -> int:
        if project:
            cursor = self.connection.execute("DELETE FROM messages WHERE project = ?", (project,))
        else:
            cursor = self.connection.execute("DELETE FROM messages")
        self.connection.commit()
        return cursor.rowcount

    def count(self, project: str = "") -> int:
        if project:
            row = self.connection.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE project = ?", (project,)
            ).fetchone()
        else:
            row = self.connection.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return int(row["n"])


# ======================================================================================
# The agent
# ======================================================================================

class Agent:
    def __init__(self, context, tools: list[Tool], memory: AgentMemory | None = None) -> None:
        self.context = context
        self.tools = {tool.name: tool for tool in tools}
        self.memory = memory or AgentMemory()
        self.model = ""
        self.temperature = 0.6
        # Set by the UI: asked before a `confirm` tool runs. Returning False skips it.
        self.confirm_hook: Callable[[Tool, dict], bool] | None = None

    # -- model ---------------------------------------------------------------------------
    def provider(self):
        provider = self.context.registry.get("ollama")
        if provider is None:
            raise RuntimeError("The Ollama provider is not registered.")
        return provider

    def available(self) -> tuple[bool, str]:
        provider = self.provider()
        if not provider.installed:
            return False, ("Ollama is not installed. It is free and runs offline — "
                           "get it from ollama.com/download.")
        if not provider.reachable():
            return False, "Ollama is installed but not running. Press Start."
        if not provider.list_models():
            return False, ("Ollama is running but has no models. Pull one, for example "
                           "llama3.1:8b.")
        return True, ""

    def chosen_model(self) -> str:
        if self.model:
            return self.model
        configured = getattr(self.context.settings, "agent_model", "")
        if configured:
            return configured
        best = self.provider().best_model(need_tools=True)
        return best.name if best else ""

    # -- the loop ---------------------------------------------------------------------------
    def run(self, user_message: str, should_cancel: Callable[[], bool] | None = None
            ) -> Iterator[AgentEvent]:
        """Yields events as it works. The UI renders them as they arrive."""
        ok, reason = self.available()
        if not ok:
            yield AgentEvent("error", reason)
            return

        project = getattr(self.context, "project_key", "")
        model = self.chosen_model()
        provider = self.provider()
        model_info = next(
            (m for m in provider.list_models() if m.name == model), None
        )
        use_tools = bool(model_info and model_info.supports_tools)

        self.memory.add(project, "user", user_message)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += self.memory.history(project, limit=30)

        tool_schemas = [tool.schema() for tool in self.tools.values()] if use_tools else None
        if not use_tools:
            yield AgentEvent(
                "status",
                f"{model} cannot call tools, so this is conversation only. Pick a "
                f"tool-capable model to let it drive the app.",
            )

        for round_index in range(MAX_ROUNDS):
            if should_cancel and should_cancel():
                yield AgentEvent("status", "Stopped.")
                return

            yield AgentEvent("status", f"Thinking ({model})" +
                             (f", step {round_index + 1}" if round_index else ""))
            try:
                message = provider.chat(
                    messages, model=model, tools=tool_schemas,
                    temperature=self.temperature,
                )
            except Exception as error:
                yield AgentEvent("error", f"The model failed: {error}")
                return

            calls = message.get("tool_calls") or []
            content = (message.get("content") or "").strip()

            if not calls:
                # Some models describe a call in prose and then invent its result. That
                # is worse than refusing, so it is caught and labelled rather than shown
                # to the user as fact.
                faked = use_tools and _looks_like_a_faked_call(content)
                if faked and round_index == 0:
                    yield AgentEvent(
                        "error",
                        f"{model} wrote a tool call as text instead of actually calling "
                        f"one, which means any result it states is invented. Pick a model "
                        f"marked [tools] and not 'unreliable' — llama3.1 or qwen3 are "
                        f"reliable here.\n\nWhat it produced:\n{content[:600]}",
                    )
                    return
                answer = content or "(the model returned nothing)"
                self.memory.add(project, "assistant", answer)
                yield AgentEvent("answer", answer)
                return

            # Keep the assistant's tool-call turn in context, or the model loses the thread.
            messages.append(message)
            if content:
                yield AgentEvent("status", content[:400])

            for call in calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                tool = self.tools.get(name)
                if tool is None:
                    result = {"error": f"There is no tool called '{name}'."}
                    yield AgentEvent("tool_result", json.dumps(result), tool=name)
                    messages.append({"role": "tool", "content": json.dumps(result)})
                    continue

                yield AgentEvent("tool_call", tool.description, tool=name, arguments=arguments)

                if tool.confirm and self.confirm_hook is not None:
                    if not self.confirm_hook(tool, arguments):
                        result = {"cancelled": "The user declined this action."}
                        yield AgentEvent("tool_result", "Declined by the user.", tool=name)
                        messages.append({"role": "tool", "content": json.dumps(result)})
                        continue

                try:
                    value = tool.handler(**arguments)
                    result = {"ok": True, "result": value}
                except TypeError as error:
                    result = {"ok": False,
                              "error": f"Wrong arguments for {name}: {error}"}
                except Exception as error:
                    result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
                    traceback.print_exc()

                text = json.dumps(result, default=str)[:MAX_RESULT_CHARS]
                yield AgentEvent("tool_result", text, tool=name, payload=result)
                messages.append({"role": "tool", "content": text})

        note = (f"Stopped after {MAX_ROUNDS} steps without a final answer. "
                f"Ask again more specifically, or try a stronger model.")
        self.memory.add(project, "assistant", note)
        yield AgentEvent("answer", note)
