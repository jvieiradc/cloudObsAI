import asyncio
import json
from pathlib import Path

import pytest

from cloudobsai.agent import AgentResult
from cloudobsai.alerts import handle_alert, signature
from cloudobsai.config import Config, Mode
from cloudobsai.core import ERROR_MESSAGE, LIMIT_MESSAGE, MENTION_WARNING, Core, choose_mode, split_memory
from cloudobsai.store import Store

SPACE = "spaces/ALLOWED"
THREAD = f"{SPACE}/threads/T1"


class FakeChat:
    def __init__(self):
        self.posts = []

    async def post(self, space, text, *, req_id, thread_name=None, thread_key=None):
        self.posts.append({"space": space, "text": text, "req_id": req_id, "thread": thread_name, "key": thread_key})
        return {"thread": {"name": THREAD}}


class FakeAgent:
    def __init__(self, text="resposta", error=None, outcome="ok"):
        self.text, self.error, self.outcome, self.prompts, self.modes = text, error, outcome, [], []

    async def run(self, prompt, mode):
        self.prompts.append(prompt)
        self.modes.append(mode.name)
        if self.error:
            raise self.error
        return AgentResult(text=self.text, tools=["mcp__grafana__x"], outcome=self.outcome)


@pytest.fixture
def config():
    return Config(
        allowed_spaces=frozenset({SPACE}),
        alert_spaces={"rede": SPACE},
        webhook_token="t",
        subscription="s",
        data_dir=Path("/tmp"),
        system_prompt_file=Path("/tmp/x"),
        explain=Mode("explicar", 4, 90, 500),
        investigate=Mode("investigar", 10, 240, 900),
        max_concurrency=2,
    )


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def event(text="explica", space=SPACE, space_type="ROOM", thread=THREAD, name="m1", user_type="HUMAN"):
    return {
        "type": "MESSAGE",
        "user": {"displayName": "Ana", "type": user_type},
        "space": {"name": space, "type": space_type},
        "message": {"name": name, "argumentText": text, "thread": {"name": thread}},
    }


ALERT = {
    "groupKey": "g1",
    "status": "firing",
    "commonLabels": {"alertname": "LinkDown"},
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "f1",
            "startsAt": "2026-10-30T10:00:00Z",
            "labels": {"alertname": "LinkDown", "space": "rede", "instance": "a"},
            "annotations": {"summary": "Link caiu"},
        }
    ],
}


def run(coro):
    return asyncio.run(coro)


def test_only_allowed_spaces_and_dm_are_accepted(config, store):
    core = Core(config, store, FakeChat(), FakeAgent())
    assert core.accepts(event()) == "m1"
    assert core.accepts(event(space_type="DM", space="spaces/any")) == "m1"
    assert core.accepts(event(space="spaces/OTHER")) is None
    assert core.accepts(event(user_type="BOT")) is None
    assert core.accepts({"type": "ADDED_TO_SPACE", "space": {"name": SPACE}}) is None


def test_duplicate_event_is_detected(store):
    assert store.mark_event("m1") is True
    assert store.mark_event("m1") is False


def test_warning_only_on_first_answer_of_a_space_thread(config, store):
    chat = FakeChat()
    core = Core(config, store, chat, FakeAgent())
    run(core.handle(event(name="m1")))
    run(core.handle(event(name="m2")))
    assert chat.posts[0]["text"].endswith(MENTION_WARNING)
    assert MENTION_WARNING not in chat.posts[1]["text"]


def test_dm_has_no_warning_and_no_thread(config, store):
    chat = FakeChat()
    core = Core(config, store, chat, FakeAgent())
    run(core.handle(event(space="spaces/dm", space_type="DM")))
    assert MENTION_WARNING not in chat.posts[0]["text"]
    assert chat.posts[0]["thread"] is None


def test_mention_in_alert_thread_uses_alert_context(config, store):
    chat, agent = FakeChat(), FakeAgent()
    run(handle_alert(ALERT, config, store, chat))
    run(Core(config, store, chat, agent).handle(event()))
    assert "LinkDown" in agent.prompts[0] and "<alert_context>" in agent.prompts[0]


def test_free_question_has_no_alert_context(config, store):
    agent = FakeAgent()
    run(Core(config, store, FakeChat(), agent).handle(event()))
    assert "<alert_context>" not in agent.prompts[0]


def test_unmapped_alert_is_not_posted(config, store):
    chat = FakeChat()
    payload = json.loads(json.dumps(ALERT))
    payload["alerts"][0]["labels"]["space"] = "outro"
    assert run(handle_alert(payload, config, store, chat)) is False
    assert chat.posts == []


def test_agent_failure_answers_short_error(config, store):
    chat = FakeChat()
    run(Core(config, store, chat, FakeAgent(error=TimeoutError())).handle(event()))
    assert chat.posts[0]["text"].startswith(ERROR_MESSAGE)


def test_turn_limit_answers_with_guidance(config, store):
    chat = FakeChat()
    run(Core(config, store, chat, FakeAgent(text="", outcome="error_max_turns")).handle(event()))
    assert chat.posts[0]["text"].startswith(LIMIT_MESSAGE)


def test_memory_note_is_hidden_and_stored_as_hypothesis(config, store):
    chat = FakeChat()
    run(handle_alert(ALERT, config, store, chat))
    agent = FakeAgent(text="Link caiu.\nMEMORIA: provedor instável")
    run(Core(config, store, chat, agent).handle(event()))
    assert "MEMORIA" not in chat.posts[-1]["text"]
    rows = store.related_incidents(signature(ALERT["alerts"][0]["labels"]), "LinkDown")
    assert [(r["summary"], r["state"]) for r in rows] == [("provedor instável", "hipotese")]


def test_register_is_confirmed_with_author_and_skips_the_agent(config, store):
    chat, agent = FakeChat(), FakeAgent()
    run(handle_alert(ALERT, config, store, chat))
    run(Core(config, store, chat, agent).handle(event(text="registra: causa foi o provedor")))
    assert agent.prompts == []
    row = store.related_incidents(signature(ALERT["alerts"][0]["labels"]), "LinkDown")[0]
    assert (row["state"], row["author"], row["summary"]) == ("confirmado", "Ana", "causa foi o provedor")


def test_new_occurrence_sees_previous_incident(config, store):
    chat, agent = FakeChat(), FakeAgent()
    run(handle_alert(ALERT, config, store, chat))
    core = Core(config, store, chat, agent)
    run(core.handle(event(text="registra: causa foi o provedor", name="m1")))
    run(core.handle(event(text="explica", name="m2")))
    assert "<incident_memory>" in agent.prompts[0] and "CONFIRMADO por Ana" in agent.prompts[0]


def test_dm_never_feeds_memory(config, store):
    chat = FakeChat()
    agent = FakeAgent(text="ok\nMEMORIA: segredo")
    core = Core(config, store, chat, agent)
    run(core.handle(event(space="spaces/dm", space_type="DM", name="m1")))
    run(core.handle(event(text="registra: segredo", space="spaces/dm", space_type="DM", name="m2")))
    assert store.search_incidents("segredo") == []


def test_full_text_search_and_delete(store):
    incident = store.add_incident("disco do cassandra cheio", "confirmado", alertname="DiskFull")
    assert [r["id"] for r in store.search_incidents("cassandra")] == [incident]
    assert store.delete_incident(incident)
    assert store.search_incidents("cassandra") == []


def test_mode_selection(config):
    assert choose_mode("explica o alerta", config).name == "explicar"
    assert choose_mode("investiga a causa", config).name == "investigar"
