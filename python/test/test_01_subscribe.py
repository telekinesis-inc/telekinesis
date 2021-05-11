from telekinesis import Broker, Telekinesis, Connection, Session, PublicUser
import asyncio
import pytest
import os

pytestmark = pytest.mark.asyncio


@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def test_subscribe():
    bro = await Broker().serve(port=8780)

    class Registry(dict):
        pass

    c = await Connection(Session(), "ws://localhost:8780")
    bro.entrypoint = await Telekinesis(Registry(), c.session)._delegate("*")

    class Counter:
        def __init__(self, initial_value=0):
            self.value = initial_value

        def increment(self, amount=1):
            self.value += amount
            return self

    pu0 = await PublicUser("ws://localhost:8780")
    await pu0.update({"Counter": Counter})

    counter = await PublicUser("ws://localhost:8780").get("Counter")(2)._subscribe()

    assert counter.value._last() == 2

    await counter.increment(3).increment(1)

    assert counter.value._last() == 6
