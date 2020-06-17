from camarere import Hub, Node
import asyncio
import pytest
import os

pytestmark = pytest.mark.asyncio

@pytest.fixture
def event_loop(): # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()

async def test_auth_publish():
    admin_node = Node()
    node = Node()

    async with Hub(root_pubkey=admin_node.connection.public_key) as hub:
        async with admin_node:
            async with node:
                try:
                    await node.publish_service('test', lambda: None)
                except Exception as e:
                    assert 'UNAUTHORIZED' in str(e)
                
                assert 'test' not in hub.services
                await admin_node.publish_service('test', lambda: None)
                assert 'test' in hub.services
                

                assert 'test' in await admin_node.list_services()
                assert 'test' not in await node.list_services()

                try:
                    await node.get_service('test')
                except Exception as e:
                    assert 'UNAUTHORIZED' in str(e)

                service = await admin_node.publish_service('test', lambda: None, can_call=[['*', 2]])
                assert 'test' in await node.list_services()

                background_task = asyncio.create_task(service.run())

                assert None == await (await node.get_service('test'))()
                
                background_task.cancel()

async def test_auth_delegate():
    admin_node = Node()
    node = Node()

    async with Hub(root_pubkey=admin_node.connection.public_key) as hub:
        async with admin_node:
            async with node:
                async def delegate_role_service(request):
                    await request.send_role_extension(('', 0), ('sub_role', 0))
                    return

                service = await admin_node.publish_service('get_role', delegate_role_service, can_call=[['*', 2]], inject_first_arg=True)
                background_task = asyncio.create_task(service.run())
                
                await (await node.get_service('get_role'))()
                assert ['sub_role', 0] in node.connection.roles

                try:
                    await node.publish_service('sub_rol', lambda: None)
                except Exception as e:
                    assert 'UNAUTHORIZED' in str(e)

                try:
                    await node.publish_service('sub_rolee', lambda: None)
                except Exception as e:
                    assert 'UNAUTHORIZED' in str(e)
                
                assert 'sub_role' not in hub.services

                await node.publish_service('sub_role', lambda: None)
                assert 'sub_role' in await admin_node.list_services()
                assert 'sub_role' in await node.list_services()

                await node.publish_service('sub_role/sub', lambda: None)
                assert 'sub_role/sub' in await admin_node.list_services()
                assert 'sub_role/sub' in await node.list_services()

                background_task.cancel()

async def test_auth_persist():
    admin_node = Node()
    node_0 = Node(auth_file_path='tmp.pem')
    node_1 = Node(auth_file_path='tmp.pem')

    async with Hub(root_pubkey=admin_node.connection.public_key) as hub:
        async with admin_node:
            async def delegate_role_service(request):
                await request.send_role_extension(('', 0), ('sub_role', 0))
                return

            service = await admin_node.publish_service('get_role', delegate_role_service, can_call=[['*', 2]], inject_first_arg=True)
            background_task = asyncio.create_task(service.run())
            
            async with node_0:
                await (await node_0.get_service('get_role'))()
                assert ['sub_role', 0] in node_0.connection.roles

            async with node_1:
                assert 'sub_role' not in hub.services

                await node_1.publish_service('sub_role', lambda: None)
                assert 'sub_role' in await admin_node.list_services()

            background_task.cancel()    
    os.remove('tmp.pem')
