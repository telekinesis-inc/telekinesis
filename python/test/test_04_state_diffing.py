from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
import os

pytestmark = pytest.mark.asyncio


@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def test_state_diffing():
    measures = {"count": 0, "size_kb": 0}

    class MeasuredBroker(Broker):
        async def handle_send(self, connection, message, source, destination):
            measures["count"] += 1
            measures["size_kb"] += len(message) / 2 ** 10
            return await super().handle_send(connection, message, source, destination)

    bro = await MeasuredBroker().serve(port=8783)

    class Container:
        def __init__(self):
            pass

    container = Container()
    c = await Connection(Session(), "ws://localhost:8783")
    bro.entrypoint = await Telekinesis(container, c.session)._delegate("*")

    registry = await Entrypoint("ws://localhost:8783")._subscribe()

    assert measures["size_kb"] < 2 ** 10

    container.x = os.urandom(2 ** 20)

    await registry
    assert 2 ** 10 < measures["size_kb"] < 1.1 * 2 ** 10
    assert registry.x._last() == container.x

    container.y = os.urandom(2 ** 20)

    await registry
    assert 2 ** 11 < measures["size_kb"] < 1.1 * 2 ** 11
    assert registry.y._last() == container.y
