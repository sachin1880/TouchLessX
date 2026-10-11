"""Tests for the assistant: understanding free requests, the listener's two-stage
hearing, and the limits on what a request can cause. No microphone, speech model,
language model or network is needed."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from src.assistant import (
    FREE_REQUEST_WORDS, AIError, LocalAI, Task, gmail_compose_url, interpret, mailto_url, site_url,
)
from src.voice import VoiceCommand, VoiceListener, strip_wake_word

APPS = ["Google Chrome", "Safari", "Notes", "Mail", "Calculator"]


def _result(spoken):
    r = interpret(spoken, APPS)
    if r is None:
        return None
    if isinstance(r, VoiceCommand):
        return ("command", r.action or r.app or r.kind)
    if r.kind == "open_url":
        url = urlparse(r.url)
        query = parse_qs(url.query)
        return ("url", url.netloc, (query.get("search_query") or query.get("q") or [""])[0])
    return (r.kind, r.topic or r.text, r.to) if r.kind == "write_email" else (r.kind, r.text)


@pytest.mark.parametrize("spoken, expected", [
    # the user's own examples
    ("Open YouTube and search for Shape of You.", ("url", "www.youtube.com", "shape of you")),
    ("write a mail about leave application for two days", ("write_email", "leave application for two days", "")),
    # YouTube
    ("open youtube", ("url", "www.youtube.com", "")),
    ("play kesariya song on youtube", ("url", "www.youtube.com", "kesariya song")),
    ("search for lo-fi music on YouTube", ("url", "www.youtube.com", "lo-fi music")),
    ("youtube search arijit singh", ("url", "www.youtube.com", "arijit singh")),
    ("Play Believer by Imagine Dragons", ("url", "www.youtube.com", "believer by imagine dragons")),
    ("can you open youtube and play the song perfect please", ("url", "www.youtube.com", "perfect")),
    # web search and sites
    ("search google for best laptops under 50,000 rupees", ("url", "www.google.com", "best laptops under 50000 rupees")),
    ("google weather in delhi", ("url", "www.google.com", "weather in delhi")),
    ("search for python tutorials", ("url", "www.google.com", "python tutorials")),
    ("look up flights to mumbai on google", ("url", "www.google.com", "flights to mumbai")),
    ("open gmail", ("url", "mail.google.com", "")),
    ("go to whatsapp", ("url", "web.whatsapp.com", "")),
    ("open facebook dot com", ("url", "facebook.com", "")),
    ("open the github website", ("url", "github.com", "")),
    # an installed application wins over a web site of the same name
    ("open notes", ("command", "Notes")),
    ("open google chrome", ("command", "Google Chrome")),
    # mail
    ("write an email to my manager about project delay", ("write_email", "project delay", "my manager")),
    ("Compose a mail to Rahul regarding tomorrow's meeting.", ("write_email", "tomorrow's meeting", "rahul")),
    ("draft an email on the topic of salary revision", ("write_email", "the topic of salary revision", "")),
    ("mail priya about the invoice", ("write_email", "the invoice", "priya")),
    # other writing, dictation, questions
    ("write a paragraph about global warming", ("write_text", "write a paragraph about global warming")),
    ("Type Hello, how are you doing today?", ("type_text", "Hello, how are you doing today?")),
    ("what is the capital of france", ("answer", "what is the capital of france")),
    ("explain how a rainbow forms", ("answer", "explain how a rainbow forms")),
    # the plain commands still work when spoken freely
    ("scroll down slowly", ("command", "scroll")),
    ("Next tab.", ("command", "next_tab")),
    ("play", ("command", "play_pause")),
    ("play the music", ("command", "play_pause")),
    ("copy", ("command", "copy")),
])
def test_free_requests_are_understood(spoken, expected):
    assert _result(spoken) == expected


def test_a_word_inside_a_longer_request_does_not_fire_a_basic_command():
    # "copy", "delete", "close tab" appear in these, but they are searches / writing, not commands
    assert _result("search google for copy machines")[0] == "url"
    assert _result("write a mail about how to delete old files")[0] == "write_email"
    assert _result("what happens if i close the tab while a download is running and then open it again")[0] == "answer"


def test_only_safe_https_addresses_can_come_out_of_a_request():
    for spoken in ("open youtube", "open gmail", "open example dot com", "search google for x", "play y"):
        task = interpret(spoken, APPS)
        assert task.url.startswith("https://") and " " not in task.url
    for bad in ("file slash slash etc passwd", "javascript alert", "localhost", "192.168.1.1", "evil dot com slash x",
                "ftp dot example dot com semicolon rm", "something made up"):
        assert site_url(bad) is None, bad
    unknown = interpret("open something made up", APPS)           # not an app, not a known site:
    assert urlparse(unknown.url).netloc == "www.google.com"       # ...so it is looked up, on Google only
    # whatever is said after "search" stays inside the query parameter
    hostile = interpret("search google for a&b=c#frag https://evil.example/?x=1", APPS)
    parsed = urlparse(hostile.url)
    assert parsed.netloc == "www.google.com" and parsed.path == "/search" and parsed.fragment == ""
    assert list(parse_qs(parsed.query)) == ["q"]


def test_mail_drafts_carry_subject_and_body_and_never_a_made_up_address():
    gmail = urlparse(gmail_compose_url("Leave request", "Dear Sir,\nTwo days.\n& thanks", "my manager"))
    query = parse_qs(gmail.query, keep_blank_values=True)
    assert gmail.netloc == "mail.google.com" and query["su"] == ["Leave request"]
    assert query["body"] == ["Dear Sir,\nTwo days.\n& thanks"] and query["to"] == [""]   # a name is not an address
    assert parse_qs(urlparse(gmail_compose_url("s", "b", "a@b.com")).query)["to"] == ["a@b.com"]
    assert mailto_url("Hi there", "Line 1\nLine 2") == "mailto:?subject=Hi%20there&body=Line%201%0ALine%202"


class _FakeTranscriber:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def transcribe(self, pcm):
        self.calls += 1
        return self.text


def _listen(constrained, free="", armed_first=False, transcriber=True):
    heard, fake = [], _FakeTranscriber(free)
    listener = VoiceListener(Path("unused"), [], on_heard=lambda k, t: heard.append((k, t)), on_state=lambda *_: None,
                             app_names=APPS, transcriber=fake if transcriber else None, free_words=FREE_REQUEST_WORDS)
    if armed_first:
        listener.handle_phrase("computer", b"", 0.0)
        heard.clear()
    listener.handle_phrase(constrained, b"\x00\x00", 1.0)
    return heard, fake.calls


def test_listener_settles_plain_commands_at_once_and_rehears_free_requests():
    assert _listen("computer scroll down slowly") == ([("command", "scroll down slowly")], 0)       # no second hearing
    assert _listen("computer open [unk] and search [unk] [unk]", "Computer, open YouTube and search for Shape of You.") \
        == ([("free", "open YouTube and search for Shape of You.")], 1)
    assert _listen("computer [unk] [unk]", "Computer, play Kesariya.") == ([("free", "play Kesariya.")], 1)
    # every word recognised, but "gmail" / "search" / "write" mark it as a free request
    assert _listen("computer open gmail", "Computer, open Gmail.") == ([("free", "open Gmail.")], 1)
    assert _listen("computer write mail about copy", "Computer, write a mail about copyright.")[0][0][0] == "free"


def test_listener_never_transcribes_speech_that_was_not_addressed_to_it():
    assert _listen("open [unk] and search [unk]", "private conversation") == ([("ignored", "open [unk] and search [unk]")], 0)
    assert _listen("[unk] [unk] [unk]", "more private talk") == ([("ignored", "[unk] [unk] [unk]")], 0)


def test_wake_word_alone_then_the_request_as_a_second_phrase():
    heard, calls = _listen("computer", "ignored")
    assert heard == [("wake", "computer")] and calls == 0
    assert _listen("[unk] [unk] [unk]", "Search Google for train tickets.", armed_first=True) \
        == ([("free", "Search Google for train tickets.")], 1)
    assert _listen("next tab", armed_first=True) == ([("command", "next tab")], 0)


def test_without_the_assistant_only_plain_commands_are_possible():
    assert _listen("computer open [unk]", transcriber=False) == ([("command", "open")], 0)   # parses to nothing later
    assert _listen("computer [unk] [unk]", transcriber=False) == ([("wake", "computer [unk] [unk]")], 0)
    assert _listen("computer next tab", transcriber=False) == ([("command", "next tab")], 0)


def test_wake_word_is_removed_from_transcribed_text():
    assert strip_wake_word("Computer, open YouTube.") == "open YouTube."
    assert strip_wake_word("Hey computer: play music") == "play music"
    assert strip_wake_word("open Gmail") == "open Gmail"


def test_local_ai_reports_clearly_when_it_is_not_there(monkeypatch):
    ai = LocalAI(host="http://127.0.0.1:9")   # nothing listens here
    monkeypatch.setattr("src.assistant.shutil.which", lambda _name: None)
    assert ai.status() == "offline"
    with pytest.raises(AIError):
        ai.write_email("anything")


def test_local_ai_output_is_parsed_and_bounded(monkeypatch):
    ai = LocalAI()
    replies = iter(['{"subject": "Leave", "body": "Dear Sir,\\\\nTwo days.\\nRegards"}', "not json at all",
                    '{"subject": "", "body": "x"}', "x" * 20000])
    monkeypatch.setattr(ai, "generate", lambda *a, **k: next(replies))
    assert ai.write_email("leave") == ("Leave", "Dear Sir,\nTwo days.\nRegards")
    for _ in range(2):
        with pytest.raises(AIError):
            ai.write_email("leave")
    assert len(ai.write_text("anything")) == 8000
    assert Task("answer", "x").needs_ai and not Task("open_url", "x").needs_ai


@pytest.mark.parametrize("heard, site", [
    # how real recognisers spelt these when the user (and test voices) said the brand name
    ("just open Flip card.", "www.flipkart.com"),
    ("open slipkirt.", "www.flipkart.com"),
    ("open Flipkart", "www.flipkart.com"),
    ("open hotster.", "www.hotstar.com"),
    ("open Zemato.", "www.zomato.com"),
    ("open MindTRA.", "www.myntra.com"),
    ("Open you tube", "www.youtube.com"),
    ("open what's app", "web.whatsapp.com"),
    ("open Insta gram", "www.instagram.com"),
    ("go to linked in", "www.linkedin.com"),
])
def test_misheard_brand_names_still_open_the_right_site(heard, site):
    task = interpret(heard, APPS)
    assert task is not None and task.kind == "open_url" and urlparse(task.url).netloc == site


@pytest.mark.parametrize("spoken, host, where, query", [
    ("open flipkart and search for shoes", "www.flipkart.com", "q", "shoes"),
    ("open Flipkirt and search for shoes.", "www.flipkart.com", "q", "shoes"),
    ("search for running shoes on amazon", "www.amazon.in", "k", "running shoes"),
    ("find iphone fifteen on flipkart", "www.flipkart.com", "q", "iphone fifteen"),
    ("open wikipedia and search for taj mahal", "en.wikipedia.org", "search", "taj mahal"),
    ("search for biryani on zomato", "www.google.com", "q", "biryani site:zomato.com"),   # no own search known
])
def test_searching_inside_a_named_site(spoken, host, where, query):
    task = interpret(spoken, APPS)
    url = urlparse(task.url)
    assert url.netloc == host and parse_qs(url.query)[where] == [query]


def test_an_unknown_thing_to_open_is_searched_for_not_refused():
    task = interpret("open Python Tutorial.", APPS)
    assert task.kind == "open_url" and parse_qs(urlparse(task.url).query)["q"] == ["python tutorial"]
    assert "not an app or site" in task.label
    assert interpret("open.", APPS) is None and interpret("open it", APPS) is None   # nothing to open


def test_short_or_unrelated_names_are_not_bent_into_a_site():
    from src.assistant import match_site

    for name in ("mint", "news", "chat", "the bank", "my files", "settings", "photos app"):
        assert match_site(name) is None, name
    assert match_site("google news") == "google news" and match_site("flip cart") == "flipkart"
