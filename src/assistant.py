"""The assistant: turns a freely spoken request into a task and carries it out.

Two layers. `interpret` recognises the common requests by their shape - open a web
site, search YouTube or the web, type some text, write a mail - without any AI, so
they are instant and predictable. Writing and open questions go to a language model
running on this computer through Ollama (`LocalAI`); nothing is sent to the internet.

Whatever is asked, the assistant can only do things from a fixed list: open an https
address it built itself, put text on the clipboard and paste it, open a mail draft,
show or speak an answer, or run one of the app's existing actions. The language model
only ever produces *text*; it is never given a way to run commands.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote, quote_plus

from .voice import VoiceCommand, match_app, parse_command

# Sites that can be opened by name. Anything else spoken as "<name> dot com" works too.
SITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
    "google maps": "https://maps.google.com", "maps": "https://maps.google.com",
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "google docs": "https://docs.google.com", "docs": "https://docs.google.com",
    "google calendar": "https://calendar.google.com", "google translate": "https://translate.google.com",
    "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com",
    "whatsapp": "https://web.whatsapp.com", "twitter": "https://x.com", "linkedin": "https://www.linkedin.com",
    "github": "https://github.com", "amazon": "https://www.amazon.in", "flipkart": "https://www.flipkart.com",
    "netflix": "https://www.netflix.com", "spotify": "https://open.spotify.com",
    "wikipedia": "https://www.wikipedia.org", "reddit": "https://www.reddit.com", "chatgpt": "https://chatgpt.com",
    "stack overflow": "https://stackoverflow.com", "hotstar": "https://www.hotstar.com",
    "prime video": "https://www.primevideo.com", "irctc": "https://www.irctc.co.in",
    "myntra": "https://www.myntra.com", "zomato": "https://www.zomato.com", "swiggy": "https://www.swiggy.com",
    "meesho": "https://www.meesho.com", "ajio": "https://www.ajio.com", "paytm": "https://paytm.com",
    "snapdeal": "https://www.snapdeal.com", "bookmyshow": "https://in.bookmyshow.com",
    "makemytrip": "https://www.makemytrip.com", "olx": "https://www.olx.in", "quora": "https://www.quora.com",
    "pinterest": "https://www.pinterest.com", "telegram": "https://web.telegram.org", "canva": "https://www.canva.com",
    "notion": "https://www.notion.so", "google meet": "https://meet.google.com", "jiocinema": "https://www.jiocinema.com",
    "jiosaavn": "https://www.jiosaavn.com", "gaana": "https://gaana.com", "naukri": "https://www.naukri.com",
    "google photos": "https://photos.google.com", "google news": "https://news.google.com",
}

# Where a site's own search lives; for the others a Google search limited to that site is used.
SITE_SEARCH = {
    "youtube": "https://www.youtube.com/results?search_query={q}", "google": "https://www.google.com/search?q={q}",
    "flipkart": "https://www.flipkart.com/search?q={q}", "amazon": "https://www.amazon.in/s?k={q}",
    "myntra": "https://www.myntra.com/{q}", "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "spotify": "https://open.spotify.com/search/{q}", "github": "https://github.com/search?q={q}",
    "google maps": "https://www.google.com/maps/search/{q}", "maps": "https://www.google.com/maps/search/{q}",
    "reddit": "https://www.reddit.com/search/?q={q}", "quora": "https://www.quora.com/search?q={q}",
    "snapdeal": "https://www.snapdeal.com/search?keyword={q}", "meesho": "https://www.meesho.com/search?q={q}",
    "pinterest": "https://www.pinterest.com/search/pins/?q={q}", "netflix": "https://www.netflix.com/search?q={q}",
}
_COMPACT_SITES = {name.replace(" ", ""): name for name in SITES}


def match_site(name: str) -> str | None:
    """The known site a spoken name means. Speech recognisers misspell brand names
    ("flip card", "hotster", "zemato"), so a close match counts as well as an exact one."""
    name = re.sub(r"^(?:the |my )", "", name.strip(" ."))
    name = re.sub(r"(?: website| site| page| web page| in (?:the )?browser| app)$", "", name)
    if name in SITES:
        return name
    compact = re.sub(r"[^a-z0-9]", "", name)
    if compact in _COMPACT_SITES:
        return _COMPACT_SITES[compact]
    if len(compact) >= 5:  # too short and anything matches something
        close = difflib.get_close_matches(compact, list(_COMPACT_SITES), n=1, cutoff=0.74)
        if close:
            return _COMPACT_SITES[close[0]]
    return None


def site_search(site: str, query: str) -> str:
    template = SITE_SEARCH.get(site)
    if template:
        return template.format(q=quote_plus(query) if "?" in template else quote(query))
    host = SITES[site].split("//", 1)[1].split("/", 1)[0].removeprefix("www.")
    return web_search(f"{query} site:{host}")


# Words that mark a request as more than a plain command; the listener sends such
# phrases to the open-vocabulary recogniser. They are also added to its wake-word
# recogniser's vocabulary so that "gmail" isn't forced into the nearest known word.
FREE_REQUEST_WORDS = sorted({w for name in SITES for w in name.split()} | {
    "search", "google", "find", "look", "write", "compose", "draft", "email", "mail", "type", "dictate", "visit",
    "what", "who", "when", "where", "why", "how", "tell", "explain", "translate", "website", "site", "dot", "com",
    "send", "message", "song", "video", "about", "play"})


@dataclass(frozen=True)
class Task:
    kind: str          # "open_url", "type_text", "write_email", "write_text", "answer"
    label: str         # what to show the user
    url: str = ""      # open_url
    text: str = ""     # type_text: the words to type; write_text / answer: the request as spoken
    topic: str = ""    # write_email
    to: str = ""       # write_email: who it is for, as spoken (a name, not an address)

    @property
    def needs_ai(self) -> bool:
        return self.kind in ("write_email", "write_text", "answer")


def _clean(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[‘’]", "'", text)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)       # 50,000 -> 50000
    text = re.sub(r"[^\w\s'.@-]", " ", text)       # drop punctuation the recogniser adds
    text = re.sub(r"\s+", " ", text).strip(" .")
    return re.sub(r"^(?:please |can you |could you |would you |hey |okay |ok |just |now |i want you to |i want to )+",
                  "", text)


def _query(text: str) -> str:
    """Tidies what is left of a search request: 'for the song kesariya please' -> 'kesariya'."""
    text = re.sub(r"^(?:for |up |about |the song |that song |song |a song called |video of |videos of |the )+", "", text)
    text = re.sub(r"(?: please| for me| now)+$", "", text)
    return text.strip(" .")


def youtube_search(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(query)


def web_search(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)


def site_url(name: str) -> str | None:
    """The address for a spoken site name, or None. Only https, only plain host names."""
    name = name.strip(" .")
    known = match_site(name)
    if known:
        return SITES[known]
    name = re.sub(r"^(?:the |my )", "", name)
    name = re.sub(r"(?: website| site| page| web page| in (?:the )?browser| app)$", "", name)
    spoken = re.sub(r"\s+dot\s+", ".", name).replace(" ", "")
    if re.fullmatch(r"(?:[a-z0-9-]+\.)+(?:com|in|org|net|io|co|ai|dev|app|edu|gov)(?:\.[a-z]{2})?", spoken):
        return "https://" + spoken
    return None


_YOUTUBE = (
    r"(?:open |go to |launch |on )?youtube(?: and| then)? (?:search|play|find|look|show)(?: me)?(?: up| for)? (?P<q>.+)",
    r"(?:search|play|find|look up|show me|put on) (?P<q>.+?) (?:on|in|from|at) youtube",
    r"(?:search|find) (?:on |in )?youtube (?:for )?(?P<q>.+)",
)
_ON_SITE = (
    r"(?:open |go to |launch |on |in )?(?P<s>[\w .'-]+?)(?: and| then)? (?:search|find|look|show)(?: me)?(?: up| for)? (?P<q>.+)",
    r"(?:search|find|look up|show me|buy|order)(?: for)? (?P<q>.+?) (?:on|in|from|at) (?P<s>[\w .'-]+)",
)
_WEB = (
    r"(?:search|look up|find) (?:for )?(?P<q>.+?) (?:on|in) (?:google|the web|the internet|chrome|safari)",
    r"(?:search|google|look up)(?: on| in)?(?: google| the web| the internet| online| chrome)? (?:for )?(?P<q>.+)",
    r"(?:open |go to )?google(?: and| then)? (?:search|find|look)(?: up| for)? (?P<q>.+)",
)
_EMAIL = (
    r"(?:write|compose|draft|send|create|prepare|make)(?: me)?(?: a| an| one)?(?: new)? (?:e-?mail|mail|gmail)"
    r"(?: to (?P<to>.+?))?(?: with)?(?: the)? (?:about|on|regarding|for|saying|that|topic|subject|asking) (?P<topic>.+)",
    r"(?:e-?mail|mail) (?P<to>.+?) (?:about|regarding|saying|that) (?P<topic>.+)",
)
_QUESTION = re.compile(r"^(?:what|who|whom|when|where|why|how|which|is|are|was|were|do|does|did|can|could|should|"
                       r"tell me|explain|define|describe|translate|summari[sz]e|calculate|give me|suggest)\b")
_PLAYBACK = {"", "music", "the music", "video", "the video", "song", "the song", "it", "pause", "next", "previous"}


def interpret(spoken: str, app_names=()) -> Task | VoiceCommand | None:
    """Works out what a freely spoken request (without the wake word) asks for.

    Returns a `Task`, one of the basic `VoiceCommand`s, or None when nothing fits - the
    caller may then hand the request to the language model as an open question.
    """
    text = _clean(spoken)
    if not text:
        return None

    for pattern in _YOUTUBE:
        match = re.fullmatch(pattern, text)
        if match and _query(match["q"]):
            query = _query(match["q"])
            return Task("open_url", f"YouTube: {query}", url=youtube_search(query))
    for pattern in _ON_SITE:  # "open flipkart and search for shoes", "search for shoes on amazon"
        match = re.fullmatch(pattern, text)
        site = match_site(match["s"]) if match else None
        if site and _query(match["q"]):
            query = _query(match["q"])
            return Task("open_url", f"{site.title()}: {query}", url=site_search(site, query))
    for pattern in _EMAIL:
        match = re.fullmatch(pattern, text)
        if match:
            to = (match["to"] or "").strip()
            return Task("write_email", f"Write a mail about {match['topic']}", topic=match["topic"].strip(), to=to)

    match = re.fullmatch(r"(?:type|dictate|write down|enter)(?: this| the following)?:? (?P<t>.+)", text)
    if match:
        typed = spoken.strip()  # keep the speaker's capitals and punctuation
        typed = re.sub(r"(?i)^.*?\b(?:type|dictate|write down|enter)(?: this| the following)?[:,]?\s+", "", typed, count=1)
        return Task("type_text", f"Type: {typed[:40]}", text=typed)

    match = re.fullmatch(r"(?:open|go to|launch|show|start|visit|take me to) (?P<n>.+)", text)
    if match:
        name = match["n"]
        app = match_app([w for w in name.split() if w not in ("the", "a", "my")], app_names)
        if app:
            return VoiceCommand("open_app", f"Open {app}", app=app)
        url = site_url(name)
        if url:
            return Task("open_url", f"Open {match_site(name) or name}", url=url)
        if len(name) > 2:
            # Not an application or a site known here. Asked to open something, the
            # useful thing is to go and find it - never to hand it to the AI as a question.
            return Task("open_url", f"Searched for \"{name}\" (not an app or site I know)", url=web_search(name))
        return None

    match = re.fullmatch(r"play (?P<q>.+)", text)
    if match and match["q"] not in _PLAYBACK and not re.search(r"\b(?:next|previous) (?:song|track)\b", text):
        query = _query(match["q"])
        return Task("open_url", f"YouTube: {query}", url=youtube_search(query))
    for pattern in _WEB:
        match = re.fullmatch(pattern, text)
        if match and _query(match["q"]):
            query = _query(match["q"])
            return Task("open_url", f"Search: {query}", url=web_search(query))

    basic = parse_command(text, app_names)
    if basic is not None and len(text.split()) <= 7:
        return basic  # a short plain command ("scroll down slowly", "next tab")

    if re.match(r"(?:write|compose|draft|create|prepare|make)\b", text):
        return Task("write_text", f"Write: {text[:48]}", text=spoken.strip())
    if _QUESTION.match(text):
        return Task("answer", f"Answer: {text[:48]}", text=spoken.strip())
    return basic


def gmail_compose_url(subject: str, body: str, to: str = "") -> str:
    return ("https://mail.google.com/mail/?view=cm&fs=1&to=" + quote(to if "@" in to else "")
            + "&su=" + quote(subject) + "&body=" + quote(body))


def mailto_url(subject: str, body: str, to: str = "") -> str:
    return "mailto:" + quote(to if "@" in to else "") + "?subject=" + quote(subject) + "&body=" + quote(body)


class AIError(RuntimeError):
    """Raised when the local language model is unavailable or gives nothing usable."""


class LocalAI:
    """A language model on this computer, reached through Ollama's local web service."""

    def __init__(self, model: str = "llama3.2:3b", host: str = "http://127.0.0.1:11434") -> None:
        self.model = model
        self.host = host

    def _request(self, path: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.host + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def status(self) -> str:
        """"ready", "no_model" (Ollama runs but the model isn't downloaded) or "offline"."""
        try:
            models = [m.get("name", "") for m in self._request("/api/tags", timeout=2.0).get("models", [])]
        except (OSError, ValueError):
            return "offline"
        return "ready" if any(name.split(":")[0] == self.model.split(":")[0] for name in models) else "no_model"

    def ensure_running(self) -> str:
        """Starts the Ollama service if it is installed but not running; returns `status()`."""
        state = self.status()
        if state != "offline":
            return state
        binary = shutil.which("ollama") or next(
            (p for p in ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama") if shutil.which(p)), None)
        if binary is None:
            return "offline"
        try:
            subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return "offline"
        for _ in range(16):
            time.sleep(0.5)
            state = self.status()
            if state != "offline":
                break
        return state

    def generate(self, prompt: str, system: str, as_json: bool = False, max_tokens: int = 450) -> str:
        if self.ensure_running() != "ready":
            raise AIError("The local AI is not available. Install Ollama and run: ollama pull " + self.model)
        payload = {
            "model": self.model, "prompt": prompt, "system": system, "stream": False,
            "keep_alive": "5m",  # then it leaves memory again - this Mac has little to spare
            "options": {"temperature": 0.4, "num_predict": max_tokens},
        }
        if as_json:
            payload["format"] = "json"
        try:
            text = self._request("/api/generate", payload).get("response", "").strip()
        except (OSError, ValueError) as exc:
            raise AIError(f"The local AI did not answer: {exc}") from exc
        if not text:
            raise AIError("The local AI returned nothing.")
        return text

    def write_email(self, topic: str, to: str = "", sender: str = "") -> tuple[str, str]:
        """(subject, body) for a mail about `topic`."""
        details = f"Topic: {topic}\n" + (f"Recipient: {to}\n" if to else "") + (
            f"Sender's name: {sender}\n" if sender else "Sender's name: unknown, sign as [Your Name]\n")
        raw = self.generate(
            details + 'Reply with JSON only: {"subject": "...", "body": "..."}',
            "You write clear, polite, concise emails in plain English. The body has a greeting, two or three "
            "short paragraphs and a sign-off. Do not invent facts, dates or names that were not given; use a "
            "bracketed placeholder such as [date] where a detail is missing.", as_json=True)
        try:
            parsed = json.loads(raw)
            subject, body = str(parsed["subject"]).strip(), str(parsed["body"]).strip()
        except (ValueError, KeyError, TypeError) as exc:
            raise AIError("The local AI did not produce a usable mail. Try again.") from exc
        if not subject or not body:
            raise AIError("The local AI did not produce a usable mail. Try again.")
        return subject[:200], body.replace("\\n", "\n")[:6000]  # a model sometimes writes the two characters \n

    def write_text(self, request: str) -> str:
        return self.generate(
            request, "Write exactly what is asked for, in plain text, ready to paste into a document. No "
            "preamble, no explanation, no markdown formatting.", max_tokens=600)[:8000]

    def answer(self, question: str) -> str:
        return self.generate(
            question, "You are a helpful voice assistant. Answer in one to three short sentences that sound "
            "natural when spoken aloud. If you are not sure, say so. You cannot browse the web or see the "
            "screen, and you have no information about today's news, weather or prices. If you are asked to "
            "do something on the computer rather than answer a question, reply only: Sorry, I didn't catch "
            "that as something I can do - please say it again.", max_tokens=160)[:1200]
