from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint, block_arg_evaluation
import asyncio
import pytest
import bson

from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio

async def run_serialize_test(host: str, port: int):
    """Test serialization functionality with specified host (determines transport type)."""
    # Determine URL based on host
    if host.startswith(('http://', 'https://')):
        url = f"{host}:{port}"
    else:
        url = f"ws://{host}:{port}"
    
    print(f"  Testing serialization with {url}")

    async with Broker() as bro:
        await bro.serve(host, port=port)

        class Registry(dict):
            pass

        bro.entrypoint, _ = await create_entrypoint(Registry(), url)

        @block_arg_evaluation
        def arg_keeps_pipeline(arg):
            return len(arg._state.pipeline) > 0
        
        class Serializer:
            def __init__(self, session):
                self._session = session
                self._data = None
            @block_arg_evaluation
            def serialize(self, arg):
                arg._block_gc = True
                self._data = bson.dumps(Telekinesis(None, self._session)._encode(arg))
            async def deserialize(self):
                return await Telekinesis(None, self._session)._decode(bson.loads(self._data))

        async with Entrypoint(url) as registry_0:
            async with Entrypoint(url) as registry_1:
                async with Entrypoint(url) as registry_2:

                    await registry_0.update({"arg_keeps_pipeline": arg_keeps_pipeline})
                    takp = await registry_1.get('arg_keeps_pipeline')
                    assert await takp(registry_1.get)

                    serializer = Serializer(registry_0._session)
                    await registry_0.update({"serializer": serializer})

                    await registry_1.update({"data": 1})
                    ser = await registry_1.get("serializer")
                    await ser.serialize(registry_1.update({"data": 2}))

                    assert await registry_1.get("data") == 1

                    await ser.deserialize()
                    assert await registry_1.get("data") == 2

                    await registry_0.update({"print": print})
                    prt = await registry_2.get("print")

                    olddata = serializer._data
                    with pytest.raises(Exception):
                        await ser.serialize(Telekinesis(prt._target, ser._session)("I shouldn't have permissions"))
                    
                    assert serializer._data == olddata


async def test_serialize_websocket():
    """Test serialization with WebSocket transport."""
    print("📦 Testing serialization with WebSocket")
    await run_serialize_test("localhost", 8785)


async def test_serialize_http():
    """Test serialization with HTTP fallback transport."""
    print("📦 Testing serialization with HTTP fallback")
    await run_serialize_test("http://localhost", 8786)
    

if __name__ == '__main__':
    asyncio.run(test_serialize_websocket())

