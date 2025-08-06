from telekinesis import Broker, Telekinesis, Connection, Session, Channel, PrivateKey
import random
import asyncio
import pytest
import os

pytestmark = pytest.mark.asyncio
random.seed(42)


@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def test_walkthrough():
    class FaultyBroker(Broker):  # Telekinesis should survive broker errors
        async def handle_send(self, *args, **kwargs):
            if random.random() < 0.01:
                self.logger.error("Gotcha!!!")
                kwargs["message"] = Exception("Random Fault Injection")
            await super().handle_send(*args, **kwargs)

    async with FaultyBroker() as broker_0:
        await broker_0.serve(port=8777)

        conn_0 = await Connection(Session(PrivateKey(name='demo')), "ws://localhost:8777")
        conn_0.RESEND_TIMEOUT = 1

        entrypoint_tk = Telekinesis(lambda x: (lambda y: x + y), conn_0.session)
        broker_0.entrypoint = await entrypoint_tk._delegate("*")

        async with FaultyBroker() as broker_1:  # Telekinesis works with clusters of Brokers
            await broker_1.serve(port=8778)
            await broker_1.add_broker("ws://localhost:8777", True)

            await asyncio.sleep(0.1)
            conn_1 = await Connection(Session(), "ws://localhost:8778")
            conn_1.RESEND_TIMEOUT = 1

            f_tk = Telekinesis(conn_1.entrypoint, conn_1.session)
            f = await f_tk._timeout(4)
            g = await f(b"Hello, ")._timeout(4)  # Telekinesis objects that return Telekinesis objects are welcome

            assert b"Hello, World" == await g(b"World")._timeout(5)

            long_message = os.urandom(2 ** 20)

            assert b"Hello, " + long_message == await g(long_message)._timeout(20)  # Telekinesis should handle big messages

            async with FaultyBroker() as broker_2:  # Yet another Broker!
                await broker_2.serve(port=8779)
                await broker_2.add_broker("ws://localhost:8777", True)
                await broker_2.add_broker("ws://localhost:8778")

                await asyncio.sleep(0.1)
                conn_2 = await Connection(Session(), "ws://localhost:8778")
                conn_2.RESEND_TIMEOUT = 1

                with pytest.raises(asyncio.TimeoutError):
                    g_2_tk = Telekinesis(g._target, conn_2.session)
                    g_2 = await g_2_tk._timeout(2)
                    await g_2_tk._close()

                delegator_tk = Telekinesis(lambda: g, conn_1.session)
                delegator_route = await delegator_tk._delegate(conn_2.session.session_key.public_serial())

                g_2_factory = Telekinesis(delegator_route, conn_2.session)
                g_2 = await g_2_factory()._timeout(4)

                assert b"Hello, World!!" == await g_2(b"World!!")._timeout(4)

                class Counter:
                    def __init__(self):
                        self.value = 0

                    def increment(self):
                        self.value += 1
                        return self

                    def to_be_masked(self):
                        return "May be sensitive"

                    def _private(self):
                        return "Sensitive stuff"

                counter_tk = Telekinesis(Counter, conn_0.session, ["to_be_masked"], max_delegation_depth=1)
                route_counter = await counter_tk._delegate(
                    conn_1.session.session_key.public_serial()
                )  # << Max delegation depth!

                counter_factory = Telekinesis(route_counter, conn_1.session)
                counter = await counter_factory()._timeout(4)

                assert await counter.increment().increment().value._timeout(4) == 2

                assert 'to_be_masked' not in counter._state.methods

                with pytest.raises(PermissionError):
                    await counter.to_be_masked()

                with pytest.raises(PermissionError):
                    c = counter.increment()
                    c._state.pipeline[0] = ("get", "to_be_masked")
                    await c

                with pytest.raises(PermissionError):
                    c = counter.increment()
                    c._state.pipeline[0] = ("get", "_private")
                    await c

                # Try to delegate
                counter_delegator_tk = Telekinesis(lambda: counter, conn_1.session)
                counter_delegator_route = await counter_delegator_tk._delegate(
                    conn_2.session.session_key.public_serial()
                )

                counter_2_factory = Telekinesis(counter_delegator_route, conn_2.session)
                counter_2 = await counter_2_factory()._timeout(4)

                with pytest.raises(asyncio.TimeoutError):  # Max delegation depth doesn't allow it!
                    await counter_2.value._timeout(2)

                # Clean up Telekinesis objects
                await counter_delegator_tk._close()
                await counter_2_factory._close()
                await counter_factory._close()
                await counter_tk._close()
                await g_2_factory._close()
                await delegator_tk._close()
                await conn_0.session.close()
                await conn_1.session.close()
                await conn_2.session.close()

            await f_tk._close()

        await entrypoint_tk._close()
