"""Service entry point: Pub/Sub input, alert webhook and the bounded analysis queue."""
import asyncio
import json
import logging

import aiohttp
from aiohttp import web
from google.cloud import pubsub_v1

from .agent import Agent
from .alerts import build_app
from .chat import ChatClient
from .config import load
from .core import Core
from .store import Store

log = logging.getLogger("cloudobsai")
WEBHOOK_PORT = 8080


async def run() -> None:
    config = load()
    store = Store(config.data_dir / "cloudobsai.db")
    loop = asyncio.get_running_loop()
    slots = asyncio.Semaphore(config.max_concurrency)
    tasks: set[asyncio.Task] = set()

    async with aiohttp.ClientSession() as session:
        chat = ChatClient(session)
        agent = Agent(store, config.system_prompt_file.read_text())
        core = Core(config, store, chat, agent)

        async def process(event: dict) -> None:
            async with slots:
                await core.handle(event)

        def schedule(event: dict) -> None:
            task = loop.create_task(process(event))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        def on_message(message) -> None:
            # The Chat ack window is short: record the event, ack, and answer later (RF11).
            try:
                event = json.loads(message.data)
                event_id = core.accepts(event)
                if event_id and store.mark_event(event_id):
                    loop.call_soon_threadsafe(schedule, event)
                elif event_id:
                    log.info("event_duplicate")
                message.ack()
            except Exception as error:
                log.error("event_failed error=%s", type(error).__name__)
                message.nack()

        runner = web.AppRunner(build_app(config, store, chat))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT).start()

        subscriber = pubsub_v1.SubscriberClient()
        flow = pubsub_v1.types.FlowControl(max_messages=8)
        future = subscriber.subscribe(config.subscription, callback=on_message, flow_control=flow)
        log.info("service_started")
        try:
            await asyncio.wrap_future(future)
        finally:
            future.cancel()
            await runner.cleanup()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
