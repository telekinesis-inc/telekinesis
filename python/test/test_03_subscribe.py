from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
import os

from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio


@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def test_subscribe():
    bro = await Broker().serve(port=8780)

    class Registry(dict):
        pass

    bro.entrypoint, _ = await create_entrypoint(Registry(), "ws://localhost:8780")

    class Counter:
        def __init__(self, initial_value=0):
            self.value = initial_value

        def increment(self, amount=1):
            self.value += amount
            return self

    registry = await Entrypoint("ws://localhost:8780")
    await registry.update({"counter": Counter(2)})

    counter0 = await Entrypoint("ws://localhost:8780").get("counter")
    counter1 = await Entrypoint("ws://localhost:8780").get("counter")._subscribe()

    assert counter1.value._last_value == 2

    await counter0.increment(3).increment(1)

    await asyncio.sleep(0.1)
    assert counter1.value._last_value == 6
