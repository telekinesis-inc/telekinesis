from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint, block_arg_evaluation
import asyncio
import pytest
import bson

from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio
BROKER_PORT = 8785

@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def test_serialize():
    bro = await Broker().serve(port=BROKER_PORT)

    class Registry(dict):
        pass

    bro.entrypoint, _ = await create_entrypoint(Registry(), f"ws://localhost:{BROKER_PORT}")

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
        def deserialize(self):
            return Telekinesis(None, self._session)._decode(bson.loads(self._data))


    registry_0 = await Entrypoint(f"ws://localhost:{BROKER_PORT}")
    registry_1 = await Entrypoint(f"ws://localhost:{BROKER_PORT}")
    registry_2 = await Entrypoint(f"ws://localhost:{BROKER_PORT}")

    await registry_0.update({"arg_keeps_pipeline": arg_keeps_pipeline})
    assert await registry_1.get("arg_keeps_pipeline")(registry_1.get)

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
    
    



