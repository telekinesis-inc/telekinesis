from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio


@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def test_encode_decode():

    bro = await Broker().serve(port=8784)

    class Registry(dict):
        pass

    bro.entrypoint, _ = await create_entrypoint(Registry(), "ws://localhost:8784")


    registry = await Entrypoint("ws://localhost:8784")
    await registry.update({"echo_type": lambda x: type(x).__name__})
    echo_type = await Entrypoint("ws://localhost:8784").get("echo_type")

    assert await echo_type(1) == 'int'
    assert await echo_type(1.0) == 'float'
    assert await echo_type('one') == 'str'

    await registry.update({"echo_key_type": lambda obj: type(list(obj.keys())[0]).__name__})
    echo_key_type = await Entrypoint("ws://localhost:8784").get("echo_key_type")

    assert await echo_key_type({(1, 2): '123'}) == 'tuple'