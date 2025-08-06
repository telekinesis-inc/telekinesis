from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio

async def test_encode_decode():

    async with Broker() as bro:
        await bro.serve(port=8784)
        class Registry(dict):
            pass

        bro.entrypoint, _ = await create_entrypoint(Registry(), "ws://localhost:8784")

        async with Entrypoint("ws://localhost:8784") as registry:
            await registry.update({"echo_type": lambda x: type(x).__name__})
            
            async with Entrypoint("ws://localhost:8784") as echo_type_ep:
                echo_type = await echo_type_ep.get("echo_type")

                assert await echo_type(1) == 'int'
                assert await echo_type(1.0) == 'float'
                assert await echo_type('one') == 'str'

            await registry.update({"echo_key_type": lambda obj: type(list(obj.keys())[0]).__name__})
            
            async with Entrypoint("ws://localhost:8784") as echo_key_type_ep:
                echo_key_type = await echo_key_type_ep.get("echo_key_type")
                assert await echo_key_type({(1, 2): '123'}) == 'tuple'
