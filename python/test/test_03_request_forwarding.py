from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def test_forwarding():
    count = {"count": 0}

    class CountedBroker(Broker):
        async def handle_send(self, *args, **kwargs):
            count["count"] += 1
            return await super().handle_send(*args, **kwargs)

    bro = await CountedBroker().serve(port=8782)

    class Registry(dict):
        pass

    c = await Connection(Session(), "ws://localhost:8782")
    bro.entrypoint = await Telekinesis(Registry(), c.session)._delegate("*")

    echo = lambda x: x

    registry = await Entrypoint("ws://localhost:8782")
    await registry.update({"echo": echo})

    count["count"] = 0
    assert await Entrypoint("ws://localhost:8782").get("echo")("hello") == "hello"
    assert count["count"] == 2 * 3
