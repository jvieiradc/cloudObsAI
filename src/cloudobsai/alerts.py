"""Alertmanager webhook: posts each alert group to its space and keeps the context per thread (RF1)."""
import hashlib
import hmac
import logging

from aiohttp import web

from .chat import request_id
from .config import Config
from .store import Store

log = logging.getLogger(__name__)

# Labels that change between occurrences of the same problem.
VOLATILE_LABELS = {"instance", "pod", "container", "id", "name", "uid"}
MAX_ALERTS_KEPT = 20


def signature(labels: dict) -> str:
    stable = sorted((k, v) for k, v in labels.items() if k not in VOLATILE_LABELS)
    return "|".join(f"{k}={v}" for k, v in stable)


def format_alert_message(payload: dict) -> str:
    alerts = payload.get("alerts") or []
    first = alerts[0] if alerts else {}
    labels = first.get("labels") or {}
    annotations = first.get("annotations") or {}
    status = "RESOLVIDO" if payload.get("status") == "resolved" else "DISPARANDO"
    lines = [f"*{status}: {labels.get('alertname', 'alerta')}*"]
    if len(alerts) > 1:
        lines[0] += f" ({len(alerts)} alertas)"
    if text := annotations.get("summary") or annotations.get("description"):
        lines.append(text)
    if first.get("startsAt"):
        lines.append(f"Início: {first['startsAt']}")
    return "\n".join(lines)


def compact_alert(payload: dict) -> dict:
    fields = ("status", "labels", "annotations", "startsAt", "endsAt", "fingerprint")
    return {
        "status": payload.get("status"),
        "commonLabels": payload.get("commonLabels"),
        "alerts": [{k: a.get(k) for k in fields} for a in (payload.get("alerts") or [])[:MAX_ALERTS_KEPT]],
    }


async def handle_alert(payload: dict, config: Config, store: Store, chat) -> bool:
    """Returns False when the alert's `space` has no Chat space mapped."""
    alerts = payload.get("alerts") or []
    if not alerts:
        return False
    labels = alerts[0].get("labels") or {}
    space = config.alert_spaces.get(labels.get("space", ""))
    if not space:
        return False

    group_key = payload.get("groupKey", "")
    thread_key = hashlib.sha256(group_key.encode()).hexdigest()[:32]
    state = ",".join(sorted(f"{a.get('fingerprint')}:{a.get('status')}" for a in alerts))
    message = await chat.post(
        space,
        format_alert_message(payload),
        req_id=request_id(group_key, payload.get("status", ""), state),
        thread_key=thread_key,
    )
    store.save_alert_thread(
        message["thread"]["name"], space, signature(labels), labels.get("alertname", ""), compact_alert(payload)
    )
    return True


def build_app(config: Config, store: Store, chat) -> web.Application:
    async def alerts_route(request: web.Request) -> web.Response:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(token, config.webhook_token):
            return web.Response(status=401)
        try:
            posted = await handle_alert(await request.json(), config, store, chat)
        except Exception as error:
            # A non-2xx makes Alertmanager retry; requestId keeps the retry from duplicating the post.
            log.error("alert_post_failed error=%s", type(error).__name__)
            return web.Response(status=502)
        log.info("alert_received posted=%s", posted)
        return web.Response(status=200)

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.add_routes([web.post("/alerts", alerts_route), web.get("/healthz", health)])
    return app
