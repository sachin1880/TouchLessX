"""Multi-step task agent — plan → execute → verify loop.

Architecture
────────────
1.  User says a command ("send Mama a WhatsApp — kal aa raha hoon").
2.  TaskAgent.run() sends the task to the local LLM with a tool-use system prompt.
3.  LLM responds with a JSON step: {"thought": "...", "action": "...", "args": {...}}.
4.  The agent executes the action via agent_computer.execute().
5.  The result is appended to the conversation history and fed back to the LLM.
6.  Repeat until action == "done" or max_steps reached.

The on_step callback lets the UI update the progress ring and status label live.

Planner reliability tips for llama3.2:3b
─────────────────────────────────────────
* The system prompt uses very short, concrete examples and strict JSON-only rule.
* _parse() strips markdown fences and finds the outermost {...} to be robust.
* The prompt is rebuilt every step with the full history so the model stays on task.
* If JSON parsing fails twice in a row, the loop exits cleanly.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Callable, NamedTuple

from . import agent_computer as _tools


# ── system prompt ─────────────────────────────────────────────────────────────
# Kept short on purpose: llama3.2:3b performs best with concise, example-heavy prompts.

_SYSTEM = """You are JARVIS, a computer automation agent. Reply ONLY with valid JSON.

FORMAT (one action per reply):
{"thought": "one sentence", "action": "ACTION_NAME", "args": {KEY: VALUE}, "done": false}

ACTIONS:
  open_url   args: {"url": "https://..."}
  type_text  args: {"text": "text to type at cursor"}
  press_key  args: {"keys": "enter"}  — other keys: tab, escape, cmd+a, cmd+c, cmd+v, cmd+t, cmd+w, cmd+l
  wait       args: {"seconds": 2}
  scroll     args: {"amount": 3}   — positive=down, negative=up
  speak      args: {"text": "message for user"}
  done       args: {"result": "summary for user"}

RULES:
1. Always start with speak() telling user what you are doing.
2. Use open_url for any web task — encode spaces as %20.
3. End every task with done() containing a clear result message.
4. Maximum 8 actions total. Be efficient.
5. ONLY output JSON. No explanation. No markdown. No extra text.

USEFUL URL PATTERNS:
  WhatsApp message : https://web.whatsapp.com/send?text=MESSAGE%20HERE
  Gmail compose    : https://mail.google.com/mail/?view=cm&su=SUBJECT&body=BODY
  YouTube search   : https://www.youtube.com/results?search_query=QUERY
  Google search    : https://www.google.com/search?q=QUERY
  Google Maps      : https://www.google.com/maps/search/QUERY
  Zomato search    : https://www.zomato.com/search?q=QUERY

URL-encode spaces as + or %20. URL-encode Hindi text too (%E0%A4%AB etc.).

EXAMPLE (task: "open YouTube and play Kesariya"):
Step 1: {"thought": "Tell user what I am doing", "action": "speak", "args": {"text": "Opening YouTube and searching for Kesariya"}, "done": false}
Step 2: {"thought": "Open YouTube search for Kesariya", "action": "open_url", "args": {"url": "https://www.youtube.com/results?search_query=Kesariya"}, "done": false}
Step 3: {"thought": "Task complete", "action": "done", "args": {"result": "YouTube opened with Kesariya search results. Click the first video to play."}, "done": true}
"""

# ── needs_planning heuristic ──────────────────────────────────────────────────
# These patterns suggest a task that requires multiple steps.

_MULTI_PATTERNS = re.compile(
    r"""
    \b(
        send\s+\w+\s+a\s+(whatsapp|message|text)|  # "send John a WhatsApp"
        message\s+\w+\s+(saying|that|ko)|           # "message John saying"
        whatsapp\s+\w|                              # "WhatsApp Mom"
        open\s+\w.*\band\b.*\b(search|find|type|write|compose|send|click)\b|
        go\s+to\s+\w.*\band\b|
        search.*\band\s+(buy|order|open|play|read)\b|
        (create|make|write)\s+a?\s*(new\s+)?(file|document|note)\b|
        (fill|complete)\s+(the|this|a)\s+(form|field)\b|
        (copy|paste)\s+.{3,}\s+(to|into|in)\b|
        book\s+(a\s+)?(cab|table|ticket|flight|hotel)\b|
        remind\s+me\s+(to|about)\b|
        set\s+(an?\s+)?alarm\b|
        download\s+\w|
        translate\s+.+\bto\b|
        karo\s+\w|                                  # Hindi: "karo"
        bhejo|likho|kholo|dhoondho                  # Hindi: send/write/open/search
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def needs_planning(text: str) -> bool:
    """Return True if *text* sounds like a multi-step task."""
    return bool(_MULTI_PATTERNS.search(text))


# ── step record ───────────────────────────────────────────────────────────────

class Step(NamedTuple):
    action: str
    args:   dict
    thought: str
    result: str


# ── agent ─────────────────────────────────────────────────────────────────────

class TaskAgent:
    """Multi-step computer automation agent.

    Parameters
    ──────────
    ai        LocalAI instance (from assistant.py)
    on_step   callback(step_idx: int, action: str, thought: str, progress: float)
              called on the UI thread via window.after() if window is given,
              otherwise called directly from the worker thread.
    on_speak  callback(text: str) — called when the agent wants to speak aloud.
    on_done   callback(result: str) — called when the task finishes.
    window    tk.Widget used to dispatch callbacks to the main thread.
    """

    def __init__(
        self,
        ai,
        on_step:  Callable[[int, str, str, float], None] | None = None,
        on_speak: Callable[[str], None]                  | None = None,
        on_done:  Callable[[str], None]                  | None = None,
        window=None,
    ) -> None:
        self._ai       = ai
        self._on_step  = on_step
        self._on_speak = on_speak
        self._on_done  = on_done
        self._window   = window
        self._stop_flag = threading.Event()

    def stop(self) -> None:
        self._stop_flag.set()

    # ── public entry point ─────────────────────────────────────────────────────

    def run_async(self, task: str, max_steps: int = 10) -> threading.Thread:
        """Start execution in a background thread and return the thread."""
        self._stop_flag.clear()
        t = threading.Thread(target=self._run, args=(task, max_steps),
                             daemon=True, name="TaskAgent")
        t.start()
        return t

    # ── private ───────────────────────────────────────────────────────────────

    def _run(self, task: str, max_steps: int) -> None:
        steps: list[Step] = []
        parse_fails = 0

        for i in range(max_steps):
            if self._stop_flag.is_set():
                self._dispatch_done("Task cancelled.")
                return

            progress = i / max_steps
            prompt   = _build_prompt(task, steps)

            # Ask the LLM for the next action
            try:
                raw  = self._ai.generate(prompt, _SYSTEM,
                                         as_json=True, max_tokens=180)
                plan = _parse(raw)
                parse_fails = 0
            except Exception:
                parse_fails += 1
                if parse_fails >= 2:
                    self._dispatch_done("Could not understand the AI response. Task stopped.")
                    return
                continue

            action  = plan.get("action", "done")
            args    = plan.get("args") or {}
            thought = plan.get("thought", "")

            # Notify UI
            self._dispatch_step(i, action, thought, progress)

            # Special: done
            if action == "done" or plan.get("done") is True:
                result = args.get("result", "Task complete.")
                self._dispatch_done(result)
                return

            # Special: speak aloud
            if action == "speak":
                text = args.get("text", "")
                if text and self._on_speak:
                    self._dispatch(self._on_speak, text)
                steps.append(Step(action, args, thought, "spoken"))
                continue

            # Execute tool
            result = _tools.execute(action, args)
            steps.append(Step(action, args, thought, result))

        # Ran out of steps
        self._dispatch_done("Task finished (reached max steps).")

    # ── dispatchers ───────────────────────────────────────────────────────────

    def _dispatch(self, cb, *args) -> None:
        if cb is None:
            return
        if self._window is not None:
            try:
                self._window.after(0, lambda: cb(*args))
            except Exception:
                cb(*args)
        else:
            cb(*args)

    def _dispatch_step(self, i, action, thought, progress) -> None:
        if self._on_step:
            self._dispatch(self._on_step, i, action, thought, progress)

    def _dispatch_done(self, result: str) -> None:
        if self._on_done:
            self._dispatch(self._on_done, result)


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_prompt(task: str, steps: list[Step]) -> str:
    history = ""
    for s in steps:
        history += f"  action={s.action} args={json.dumps(s.args, ensure_ascii=False)}\n"
        history += f"  result: {s.result}\n"
    return (
        f"TASK: {task}\n\n"
        + (f"STEPS DONE SO FAR:\n{history}\n" if steps else "")
        + "What is the NEXT action? Reply with JSON only."
    )


def _parse(raw: str) -> dict:
    """Parse LLM output robustly — strips markdown fences, finds outermost {}."""
    raw = raw.strip()
    # Strip ```json ... ``` or ``` ... ```
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    # Find first { ... last }
    start = raw.find("{")
    end   = raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    obj = json.loads(raw[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("parsed value is not a dict")
    return obj
