"""Decides what to do with each Chat event (SPEC 6.1, RF2-RF7, RF9-RF13)."""
import json
import logging
import re
import sqlite3

from .chat import request_id
from .config import Config, Mode
from .store import Store, format_incident

log = logging.getLogger(__name__)

MENTION_WARNING = "\n\n_Para continuar nesta thread, mencione sempre o app: sem a menção, ele não recebe a mensagem._"
ERROR_MESSAGE = "Não consegui concluir a análise agora. Tente de novo em instantes."
LIMIT_MESSAGE = "Precisei de mais consultas do que o limite permite. Tente restringir: um serviço, um alerta ou uma janela menor."
HELP_MESSAGE = (
    "Me diga o que analisar, por exemplo: *quais alertas estão disparando?* "
    "Para falar de um alerta, responda à mensagem dele citando-a e me mencione."
)
DEFAULT_REQUEST = "Explique isto em poucas linhas e diga o estado atual."
INVESTIGATE_WORDS = re.compile(r"\b(investig\w*|aprofund\w*|causa\w*|por que|porque|detalh\w*|analis\w*)\b", re.I)
REGISTER_COMMAND = re.compile(r"^\s*registr\w*\s*:?\s+(?P<text>.+)", re.I | re.S)
# Alert messages posted by the Alertmanager bridge: "(WARNING) AlertName - Firing".
QUOTED_ALERT = re.compile(r"^\s*\(\w+\)\s+(?P<name>.+?)\s+-\s+(?:firing|resolved)\b", re.I)
MEMORY_LINE = re.compile(r"^\s*MEMORIA:\s*(?P<text>.+?)\s*$", re.M)


def choose_mode(text: str, config: Config) -> Mode:
    return config.investigate if INVESTIGATE_WORDS.search(text) else config.explain


def split_memory(answer: str) -> tuple[str, str | None]:
    """Takes the agent's memory note out of the text the user sees."""
    match = None
    for match in MEMORY_LINE.finditer(answer):
        pass
    if not match:
        return answer.strip(), None
    return MEMORY_LINE.sub("", answer).strip(), match.group("text")


def quoted_text(message: dict) -> str:
    """Text of the message being replied to, which also covers alerts posted by other senders."""
    snapshot = (message.get("quotedMessageMetadata") or {}).get("quotedMessageSnapshot") or {}
    return (snapshot.get("text") or snapshot.get("formattedText") or "").strip()


def quoted_alertname(quoted: str) -> str:
    match = QUOTED_ALERT.match(quoted)
    return match.group("name").lower() if match else ""


def build_prompt(
    text: str, thread: sqlite3.Row | None, turns: list, incidents: list, quoted: str = "", query_budget: int = 0
) -> str:
    sections = []
    if query_budget:
        sections.append(f"<budget>Até {query_budget} rodadas de consulta. Depois, responda com o que tiver.</budget>")
    if thread is not None and thread["alert_json"]:
        sections.append(f"<alert_context>\n{thread['alert_json']}\n</alert_context>")
    if quoted:
        sections.append(f"<quoted_message>\n{quoted}\n</quoted_message>")
    if incidents:
        memory = "\n".join(format_incident(row) for row in incidents)
        sections.append(f"<incident_memory>\n{memory}\n</incident_memory>")
    if turns:
        history = [{"pergunta": t["question"], "resposta": t["answer"]} for t in turns]
        sections.append(f"<previous_turns>\n{json.dumps(history, ensure_ascii=False)}\n</previous_turns>")
    sections.append(f"<request>\n{text}\n</request>")
    return "\n\n".join(sections)


class Core:
    def __init__(self, config: Config, store: Store, chat, agent):
        self._config = config
        self._store = store
        self._chat = chat
        self._agent = agent

    def accepts(self, event: dict) -> str | None:
        """Returns the dedup ID for an event that deserves an answer, else None."""
        if event.get("type") != "MESSAGE" or (event.get("user") or {}).get("type") == "BOT":
            return None
        space = event.get("space") or {}
        is_dm = space.get("type") == "DM" or space.get("spaceType") == "DIRECT_MESSAGE"
        if not is_dm and space.get("name") not in self._config.allowed_spaces:
            log.info("event_ignored reason=space_not_allowed")
            return None
        return (event.get("message") or {}).get("name")

    async def handle(self, event: dict) -> None:
        message = event["message"]
        space = event["space"]
        is_dm = space.get("type") == "DM" or space.get("spaceType") == "DIRECT_MESSAGE"
        user = (event.get("user") or {}).get("displayName") or "desconhecido"
        text = (message.get("argumentText") or message.get("text") or "").strip()
        thread_name = (message.get("thread") or {}).get("name") or ""
        event_id = message["name"]
        quoted = quoted_text(message)

        register = REGISTER_COMMAND.match(text) if not is_dm else None
        if register:
            answer = self._register(register.group("text").strip(), thread_name, user, quoted_alertname(quoted))
            audit = {"mode": "registro"}
        elif not text and not quoted and not self._is_alert_thread(thread_name, is_dm):
            answer, audit = HELP_MESSAGE, {"mode": "ajuda"}
        else:
            answer, audit = await self._analyze(text or DEFAULT_REQUEST, thread_name, is_dm, quoted)

        if not is_dm and thread_name and self._store.claim_warning(thread_name, space["name"]):
            answer += MENTION_WARNING

        result = audit.pop("result", "ok")
        try:
            await self._chat.post(
                space["name"],
                answer,
                req_id=request_id(event_id, "reply"),
                thread_name=None if is_dm else thread_name,
            )
        except Exception as error:
            result = f"chat_error:{type(error).__name__}"
        self._store.audit(event_id=event_id, space=space["name"], user_name=user, result=result, **audit)
        log.info("request_done mode=%s result=%s quoted=%s tools=%s", audit.get("mode"), result, bool(quoted), audit.get("tools"))

    def _is_alert_thread(self, thread_name: str, is_dm: bool) -> bool:
        thread = self._store.thread(thread_name) if thread_name and not is_dm else None
        return thread is not None and bool(thread["alert_json"])

    def _register(self, summary: str, thread_name: str, author: str, alertname: str = "") -> str:
        thread = self._store.thread(thread_name) if thread_name else None
        self._store.add_incident(
            summary,
            "confirmado",
            signature=(thread["signature"] or "") if thread else "",
            alertname=(thread["alertname"] or "") if thread and thread["alertname"] else alertname,
            thread_name=thread_name or None,
            author=author,
        )
        return f"Registrado como confirmado por {author}."

    async def _analyze(self, text: str, thread_name: str, is_dm: bool, quoted: str = "") -> tuple[str, dict]:
        mode = choose_mode(text, self._config)
        thread = self._store.thread(thread_name) if thread_name and not is_dm else None
        is_alert = thread is not None and bool(thread["alert_json"])
        alertname = thread["alertname"] if is_alert else "" if is_dm else quoted_alertname(quoted)
        signature_ = thread["signature"] if is_alert else ""
        incidents = self._store.related_incidents(signature_, alertname) if signature_ or alertname else []
        turns = self._store.recent_turns(thread_name) if thread_name else []
        prompt = build_prompt(text, thread, turns, incidents, quoted, max(mode.max_turns - 2, 1))
        try:
            result = await self._agent.run(prompt, mode)
        except Exception as error:
            log.error("agent_failed error=%s", type(error).__name__)
            return ERROR_MESSAGE, {"mode": mode.name, "result": f"agent_error:{type(error).__name__}"}
        usage = {
            "mode": mode.name,
            "tools": ",".join(result.tools),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
        }
        if result.outcome != "ok" or not result.text:
            log.error("agent_failed outcome=%s", result.outcome)
            message = LIMIT_MESSAGE if result.outcome == "error_max_turns" else ERROR_MESSAGE
            return message, {**usage, "result": f"agent_error:{result.outcome}"}

        answer, note = split_memory(result.text)
        if thread_name:
            self._store.add_turn(thread_name, text, answer)
        # Only identified alerts feed the memory, so free questions and DMs never leak into a space.
        if alertname and note:
            self._store.add_incident(
                note, "hipotese", signature=signature_, alertname=alertname, thread_name=thread_name
            )
        return answer, usage
