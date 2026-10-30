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

_SYSTEM = """You are JARVIS, a real computer automation agent running on macOS. Reply ONLY with valid JSON.

FORMAT (one step per reply):
{"thought": "one sentence", "action": "ACTION_NAME", "args": {KEY: VALUE}, "done": false}

═══ ACTIONS ════════════════════════════════════════════════════════════════

MESSAGING (real send — no browser needed):
  send_imessage      args: {"to": "mom", "message": "Kal aa raha hoon"}
  whatsapp_send      args: {"to": "mom", "message": "Kal aa raha hoon"}
    — looks up contact, opens WhatsApp Web, sends automatically

EMAIL (real send):
  send_email_native  args: {"to": "friend@gmail.com", "subject": "...", "body": "..."}
    — sends via macOS Mail.app (must be configured with an account)
  send_email_gmail   args: {"to": "name or email", "subject": "...", "body": "..."}
    — opens Gmail compose in browser

CONTACTS:
  lookup_contact  args: {"name": "mom"}
    — returns phone and email stored for that name
  add_contact     args: {"name": "mom", "phone": "+919876543210", "email": ""}
    — saves contact for future use

APPS & SYSTEM:
  open_app     args: {"name": "Finder"}  — launch any macOS app by name
  open_folder  args: {"path": "~/Documents/MyProject"}
  shell        args: {"command": "open -a 'Visual Studio Code' ~/Documents/MyProject"}
    — allowed: open, ls, say, echo, afplay, cat, date

BROWSER & WEB:
  open_url      args: {"url": "https://..."}
  google_search args: {"query": "best restaurants near me"}

KEYBOARD & MOUSE:
  type_text  args: {"text": "Hello World"}   — types at current cursor
  press_key  args: {"keys": "enter"}          — enter, tab, escape, cmd+t, cmd+w, cmd+l, cmd+a, cmd+c, cmd+v
  scroll     args: {"amount": 3}              — positive=down, negative=up
  click      args: {"x": 500, "y": 400}
  wait       args: {"seconds": 2}

VOICE FEEDBACK:
  speak  args: {"text": "message for the user"}
  done   args: {"result": "final summary for the user"}

═══ RULES ══════════════════════════════════════════════════════════════════
1. Always start with speak() — tell the user what you are doing.
2. If the task mentions a person by name (mom, dad, Rahul), call lookup_contact FIRST.
3. If contact is not found and a phone/email is needed, call speak() asking the user for it, then done().
4. For WhatsApp: use whatsapp_send — it handles everything automatically.
5. For email: use send_email_native first; if that fails use send_email_gmail.
6. End EVERY task with done() containing a clear result.
7. Maximum 10 steps. Be efficient — prefer direct actions over indirect ones.
8. ONLY output JSON. No explanation text. No markdown. No prose.

═══ EXAMPLES ═══════════════════════════════════════════════════════════════

Task: "Send WhatsApp to mom — kal aa raha hoon"
{"thought": "Tell user I am sending WhatsApp", "action": "speak", "args": {"text": "WhatsApp bhej raha hoon mom ko"}, "done": false}
{"thought": "Look up mom's contact", "action": "lookup_contact", "args": {"name": "mom"}, "done": false}
{"thought": "Send WhatsApp with the message", "action": "whatsapp_send", "args": {"to": "mom", "message": "Kal aa raha hoon"}, "done": false}
{"thought": "Done", "action": "done", "args": {"result": "WhatsApp sent to mom successfully!"}, "done": true}

Task: "Open VS Code and my project"
{"thought": "Tell user", "action": "speak", "args": {"text": "VS Code aur project open kar raha hoon"}, "done": false}
{"thought": "Open VS Code with project folder", "action": "shell", "args": {"command": "open -a 'Visual Studio Code' ~/Documents"}, "done": false}
{"thought": "Done", "action": "done", "args": {"result": "VS Code opened."}, "done": true}

Task: "Send email to Rahul about meeting tomorrow"
{"thought": "Tell user", "action": "speak", "args": {"text": "Rahul ko email bhej raha hoon"}, "done": false}
{"thought": "Look up Rahul's email", "action": "lookup_contact", "args": {"name": "rahul"}, "done": false}
{"thought": "Send email via Mail app", "action": "send_email_native", "args": {"to": "rahul", "subject": "Meeting Tomorrow", "body": "Hi Rahul,\\n\\nLet's meet tomorrow. Please confirm the time.\\n\\nThanks"}, "done": false}
{"thought": "Done", "action": "done", "args": {"result": "Email sent to Rahul about tomorrow's meeting."}, "done": true}
"""

# ── needs_planning heuristic ──────────────────────────────────────────────────
# These patterns suggest a task that requires multiple steps.
# Covers English, Hindi (Devanagari) and Hinglish (Roman-script Hindi).

_MULTI_PATTERNS = re.compile(
    r"""
    \b(
        # ── English multi-step patterns ──────────────────────────────────────
        send\s+\w+\s+a\s+(whatsapp|message|text)|   # "send John a WhatsApp"
        message\s+\w+\s+(saying|that|ko)|            # "message John saying"
        whatsapp\s+\w|                               # "WhatsApp Mom"
        open\s+\w.*\band\b.*\b(search|find|type|write|compose|send|click|play|watch|buy|order)\b|
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

        # ── Hinglish (Roman-script Hindi) multi-step ──────────────────────────
        # Send / message
        bhejo|message\s+karo|msg\s+karo|
        whatsapp\s+(karo|pe|par|mein)|
        # Open + action
        kholo\s+aur|khol\s+ke|
        # Write / type
        likho|type\s+karo|likh\s+do|
        # Search / find
        dhoondho|search\s+karo|dhundh\s+do|
        # Play
        chalao|play\s+karo|suno\s+\w|
        # Book / order
        book\s+karo|order\s+karo|
        # Download
        download\s+karo|
        # Translate
        translate\s+karo|anuvad\s+karo|
        # Reminder / alarm
        yaad\s+dilao|alarm\s+lagao|reminder\s+set\s+karo|
        # General "do X" in Hinglish
        \w+\s+karo\s+aur|\w+\s+kar\s+aur|

        # ── Devanagari (pure Hindi) multi-step ────────────────────────────────
        [ऀ-ॿ]+.*[ऀ-ॿ]+   # two or more Hindi words = full Hindi task
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

        # Detect language once and build the system prompt for this task.
        system = _build_system(task)

        for i in range(max_steps):
            if self._stop_flag.is_set():
                self._dispatch_done("Task cancelled.")
                return

            progress = i / max_steps
            prompt   = _build_prompt(task, steps)

            # Ask the LLM for the next action
            try:
                raw  = self._ai.generate(prompt, system,
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

def _build_system(task: str) -> str:
    """Return the system prompt for *task*, with a language instruction prepended.

    Detects Hindi / Hinglish in the task text and tells the AI to:
      • speak and reply in the same language
      • URL-encode Hindi text correctly in WhatsApp / Gmail links
    """
    try:
        from .agent_lang import detect_language, get_language_instruction
        lang = detect_language(task)
        lang_instr = get_language_instruction(lang)
    except Exception:
        lang_instr = ""

    # ── Saved contacts context ────────────────────────────────────────────────
    try:
        from .agent_contacts import summary as _contacts_summary
        contacts_hint = "\n\nSAVED CONTACTS: " + _contacts_summary()
    except Exception:
        contacts_hint = ""

    if not lang_instr:
        return _SYSTEM + contacts_hint

    hindi_hint = (
        "\nIMPORTANT for Hindi tasks:\n"
        "- WhatsApp/speak messages should be in Hindi (Devanagari or Roman).\n"
        "- URL-encode Hindi text: %E0%A4%AB etc.\n"
    ) if lang in ("hindi", "hinglish") else ""

    return lang_instr + "\n\n" + _SYSTEM + hindi_hint + contacts_hint


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
