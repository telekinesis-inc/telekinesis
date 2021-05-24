from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def test_garbage_collection():
    bro = await Broker().serve(port=8781)

    class Registry(dict):
        pass

    c = await Connection(Session(), "ws://localhost:8781")
    bro.entrypoint = await Telekinesis(Registry(), c.session)._delegate("*")

    class Counter:
        def __init__(self, initial_value=0):
            self.value = initial_value

        def increment(self, amount=1):
            self.value += amount
            return self

    registry = await Entrypoint("ws://localhost:8781")
    await registry.update({"Counter": Counter})

    assert len(registry._session.targets) == 1

    counter = await Entrypoint("ws://localhost:8781").get("Counter")(2)

    assert len(registry._session.targets) == 2

    del counter

    await asyncio.sleep(0.1)
    assert len(registry._session.targets) == 1
