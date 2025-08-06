from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
import os

from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio

async def test_state_diffing():
    measures = {"count": 0, "size_kb": 0}

    class MeasuredBroker(Broker):
        async def handle_send(self, connection, message, source, destination):
            measures["count"] += 1
            measures["size_kb"] += len(message) / 2 ** 10
            return await super().handle_send(connection, message, source, destination)


    async with MeasuredBroker() as bro:
        await bro.serve(port=8783)

        class Container:
            def set(self, attr, val):
                self.__setattr__(attr, val)
            def add_method(self, doc):
                self.method = lambda x: x
                self.method.__doc__ = doc

        bro.entrypoint, container = await create_entrypoint(Container(), "ws://localhost:8783")

        async with Entrypoint("ws://localhost:8783") as registry_ep:
            registry = await registry_ep._subscribe()

            assert measures["size_kb"] < 2 ** 10

            await container.set("x", os.urandom(2 ** 20))

            await registry
            assert 2 ** 10 < measures["size_kb"] < 1.1 * 2 ** 10
            assert registry.x._last_value == container.x._last_value

            await container.set("y", os.urandom(2 ** 20))

            await registry
            assert 2 ** 11 < measures["size_kb"] < 1.1 * 2 ** 11
            assert registry.y._last_value == container.y._last_value

            await container.add_method("some docstring")
            
            await registry
            assert registry._state.methods.get('method')[1] == "some docstring"

            await container._session.close()

if __name__ == '__main__':
    asyncio.run(test_state_diffing())
