from telekinesis import Hub, Portal
import asyncio
import pytest

pytestmark = pytest.mark.asyncio

@pytest.fixture
def event_loop(): # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()

async def test_input_request():
    async with Hub():
            async with Portal() as portal:
                async def input_requester(remote_controller):
                    return await remote_controller.send_input_request()
                
                service = await portal.publish('input_requester', input_requester, n_workers=1, inject_first_arg=True)

                caller = await portal.get('input_requester')

                caller._on_input_request = lambda prompt: 'input response'

                assert (await caller()) == 'input response'

                service.stop_all()

async def test_secret_request():
    async with Hub():
            async with Portal() as portal:
                async def secret_requester(remote_controller):
                    return await remote_controller.send_input_request(hashed=True)
                
                service = await portal.publish('input_requester', secret_requester, n_workers=1, inject_first_arg=True)

                caller = await portal.get('input_requester')

                caller._on_secret_request = lambda prompt, salt=None: 'secret'

                assert (await caller()) == 'secret'

                service.stop_all()