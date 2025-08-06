from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio


async def test_forwarding():
    count = {"count": 0}

    class CountedBroker(Broker):
        async def handle_send(self, *args, **kwargs):
            count["count"] += 1
            return await super().handle_send(*args, **kwargs)

    async with CountedBroker() as bro:
        await bro.serve(port=8782)

        class Registry(dict):
            pass

        bro.entrypoint, _ = await create_entrypoint(Registry(), "ws://localhost:8782")

        echo = lambda x: x

        async with Entrypoint("ws://localhost:8782") as registry:
            await registry.update({"echo": echo})

            count["count"] = 0
            async with Entrypoint("ws://localhost:8782") as echo_ep:
                result = await echo_ep.get("echo")("hello")
                assert result == "hello"
                assert count["count"] == 2 * 3

