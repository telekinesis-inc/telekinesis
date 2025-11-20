from telekinesis import Broker, Telekinesis, Connection, Session, Channel, PrivateKey
import random
import asyncio
import pytest
import os

pytestmark = pytest.mark.asyncio
random.seed(42)


async def run_walkthrough_test(host_template_1: str, host_template_2: str, start_port: int):
    """Test walkthrough with two different host templates for broker communication patterns."""
    
    # Generate URLs for different brokers
    def make_url(host_template: str, port: int) -> str:
        if host_template.startswith(('http://', 'https://')):
            return f"{host_template}:{port}"
        else:
            return f"ws://{host_template}:{port}"
    
    url_0 = make_url(host_template_1, start_port)
    url_1 = make_url(host_template_2, start_port + 1) 
    url_2 = make_url(host_template_1, start_port + 2)  # Use template_1 for broker_2
    
    print(f"  Testing broker communication: {url_0} <-> {url_1} <-> {url_2}")
    
    class FaultyBroker(Broker):  # Telekinesis should survive broker errors
        async def handle_send(self, *args, **kwargs):
            if random.random() < 0.01:
                self.logger.error("Gotcha!!!")
                raise Exception("Random Fault Injection")
            await super().handle_send(*args, **kwargs)

    async with FaultyBroker() as broker_0:
        await broker_0.serve(host_template_1, port=start_port)

        conn_0 = await Connection(Session(PrivateKey(name='demo')), url_0)
        conn_0.RESEND_TIMEOUT = 0.5
        conn_0.MAX_SEND_RETRIES = 6

        entrypoint_tk = Telekinesis(lambda x: (lambda y: x + y), conn_0.session)
        broker_0.entrypoint = await entrypoint_tk._delegate("*")

        async with FaultyBroker() as broker_1:  # Telekinesis works with clusters of Brokers
            await broker_1.serve(host_template_2, port=start_port + 1)
            await broker_1.add_broker(url_0, True)

            await asyncio.sleep(0.1)
            conn_1 = await Connection(Session(), url_1)
            conn_1.RESEND_TIMEOUT = .5
            conn_1.MAX_SEND_RETRIES = 6
            print('>>>>>>>>>>>>>>> about to connect to ep')

            f_tk = Telekinesis(conn_1.entrypoint, conn_1.session)
            f = await f_tk._timeout(8)
            print('>>>>>>>>>>>>>>> connected')
            g = await f(b"Hello, ")._timeout(10)  # Telekinesis objects that return Telekinesis objects are welcome
            print('>>>>>>>>>>>>>>> sent tk object')

            assert b"Hello, World" == await g(b"World")._timeout(8)
            print('>>>>>>>>>>>>>>> passed assertion')

            long_message = os.urandom(2 ** 20)

            print('>>>>>>>>>>>>>>> about to send large message')
            assert b"Hello, " + long_message == await g(long_message)._timeout(20)  # Telekinesis should handle big messages
            print('>>>>>>>>>>>>>>> large message received')

            async with FaultyBroker() as broker_2:  # Yet another Broker!
                await broker_2.serve(host_template_1, port=start_port + 2)
                await broker_2.add_broker(url_0, True)
                await broker_2.add_broker(url_1)

                await asyncio.sleep(0.1)
                conn_2 = await Connection(Session(), url_1)
                conn_2.RESEND_TIMEOUT = .5
                conn_2.MAX_SEND_RETRIES = 6
                print('>>>>>>>>>>>>>>> trying to fail')

                with pytest.raises(asyncio.TimeoutError):
                    g_2_tk = Telekinesis(g._target, conn_2.session)
                    g_2 = await g_2_tk._timeout(8)
                    await g_2_tk._close()
                print('>>>>>>>>>>>>>>> failed successfully')

                delegator_tk = Telekinesis(lambda: g, conn_1.session)
                delegator_route = await delegator_tk._delegate(conn_2.session.session_key.public_serial())

                g_2_factory = Telekinesis(delegator_route, conn_2.session)
                g_2 = await g_2_factory()._timeout(8)

                print('>>>>>>>>>>>>>>> ready to assert again with relay')
                assert b"Hello, World!!" == await g_2(b"World!!")._timeout(8)
                print('>>>>>>>>>>>>>>> asserted with relay')

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
                counter = await counter_factory()._timeout(8)

                print('>>>>>>>>>>>>>>> testing counter')
                assert await counter.increment().increment().value._timeout(8) == 2
                print('>>>>>>>>>>>>>>> testing counter done')

                assert 'to_be_masked' not in counter._state.methods

                with pytest.raises(Exception):#PermissionError):
                    print('>>>>>>>>>>>>>>> to be masked 1')
                    await counter.to_be_masked()._timeout(8)

                with pytest.raises(Exception):#PermissionError):
                    print('>>>>>>>>>>>>>>> to be masked 2')
                    c = counter.increment()
                    c._state.pipeline[0] = ("get", "to_be_masked")
                    await c._timeout(8)

                with pytest.raises(Exception):#PermissionError):
                    print('>>>>>>>>>>>>>>> _private')
                    c = counter.increment()
                    c._state.pipeline[0] = ("get", "_private")
                    await c._timeout(8)

                print('>>>>>>>>>>>>>>> trying to delegate')
                # Try to delegate
                counter_delegator_tk = Telekinesis(lambda: counter, conn_1.session)
                counter_delegator_route = await counter_delegator_tk._delegate(
                    conn_2.session.session_key.public_serial()
                )

                counter_2_factory = Telekinesis(counter_delegator_route, conn_2.session)
                counter_2 = await counter_2_factory()._timeout(8)

                with pytest.raises(Exception):#asyncio.TimeoutError):  # Max delegation depth doesn't allow it!
                    await counter_2.value._timeout(8)

                print('>>>>>>>>>>>>>>> closing')

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


        await entrypoint_tk._close()


async def test_walkthrough_ws_ws():
    """Test walkthrough with WebSocket <-> WebSocket broker communication."""
    print("🔀 Testing WebSocket <-> WebSocket broker communication")
    await run_walkthrough_test("localhost", "localhost", 8777)


async def test_walkthrough_ws_http():
    """Test walkthrough with WebSocket <-> HTTP broker communication."""
    print("🔀 Testing WebSocket <-> HTTP broker communication")  
    await run_walkthrough_test("localhost", "http://localhost", 8780)

async def test_walkthrough_http_ws():
    """Test walkthrough with WebSocket <-> HTTP broker communication."""
    print("🔀 Testing WebSocket <-> HTTP broker communication")  
    await run_walkthrough_test("http://localhost", "localhost", 8783)

async def test_walkthrough_http_http():
    """Test walkthrough with HTTP <-> HTTP broker communication."""
    print("🔀 Testing HTTP <-> HTTP broker communication")
    await run_walkthrough_test("http://localhost", "http://localhost", 8783)


if __name__ == '__main__':
    asyncio.run(test_walkthrough_ws_ws())
