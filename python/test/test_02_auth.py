from telekinesis import Hub, Portal
import asyncio
import pytest
import os

pytestmark = pytest.mark.asyncio

@pytest.fixture
def event_loop(): # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()

async def test_auth_publish():
    admin_portal = Portal()
    portal = Portal()

    async with Hub(root_pubkey=admin_portal.session.public_key) as hub:
        async with admin_portal:
            async with portal:
                try:
                    await portal.publish('test', lambda: None)
                except Exception as e:
                    assert 'UNAUTHORIZED' in str(e)
                
                assert 'test' not in hub.services
                await admin_portal.publish('test', lambda: None, n_workers=0)
                assert 'test' in hub.services
                

                assert 'test' in await admin_portal.list()
                assert 'test' not in await portal.list()

                try:
                    await portal.get('test')
                except Exception as e:
                    assert 'UNAUTHORIZED' in str(e)

                service = await admin_portal.publish('test', lambda: None, can_call=[['*', 2]], replace=True)
                assert 'test' in await portal.list()

                assert None == await (await portal.get('test'))()
                
                service.stop_all()

async def test_auth_delegate():
    admin_portal = Portal()
    portal = Portal()

    async with Hub(root_pubkey=admin_portal.session.public_key) as hub:
        async with admin_portal:
            async with portal:
                async def delegate_role(request):
                    await request.send_role_extension(('', 0), ('sub_role', 0))
                    return

                service = await admin_portal.publish('get_role', delegate_role, can_call=[['*', 2]], inject_first_arg=True)
                
                await (await portal.get('get_role'))()
                assert ['sub_role', 0] in portal.session.roles

                try:
                    await portal.publish('sub_rol', lambda: None)
                except Exception as e:
                    assert 'UNAUTHORIZED' in str(e)

                try:
                    await portal.publish('sub_rolee', lambda: None)
                except Exception as e:
                    assert 'UNAUTHORIZED' in str(e)
                
                assert 'sub_role' not in hub.services

                await portal.publish('sub_role', lambda: None, n_workers=0)
                assert 'sub_role' in await admin_portal.list()
                assert 'sub_role' in await portal.list()

                await portal.publish('sub_role/sub', lambda: None, n_workers=0)
                assert 'sub_role/sub' in await admin_portal.list()
                assert 'sub_role/sub' in await portal.list()

                service.stop_all()

async def test_auth_persist():
    admin_portal = Portal()
    portal_0 = Portal(auth_file_path='tmp.pem')
    portal_1 = Portal(auth_file_path='tmp.pem')

    async with Hub(root_pubkey=admin_portal.session.public_key) as hub:
        async with admin_portal:
            async def delegate_role(request):
                await request.send_role_extension(('', 0), ('sub_role', 0))
                return

            service = await admin_portal.publish('get_role', delegate_role, can_call=[['*', 2]], inject_first_arg=True)
            
            async with portal_0:
                await (await portal_0.get('get_role'))()
                assert ['sub_role', 0] in portal_0.session.roles

            async with portal_1:
                assert 'sub_role' not in hub.services

                await portal_1.publish('sub_role', lambda: None, n_workers=0)
                assert 'sub_role' in await admin_portal.list()

            service.stop_all()
    os.remove('tmp.pem')

# async def test_auth_can_serve():
#     admin_portal = Portal()
#     portal = Portal()

#     async with Hub(root_pubkey=admin_portal.session.public_key) as hub:
#         async with admin_portal:
#             async with portal:
#                 async def delegate_serve_role(request):
#                     await request.send_role_extension(('', 0), ('echo', 1)) # role level 1 allows to provide a service without modifying it
#                     return

#                 service = await admin_portal.publish('get_role', delegate_serve_role, can_call=[['*', 2]], inject_first_arg=True)

#                 await (await portal.get('get_role'))()
#                 try: # portal can't create the service
#                     await portal.publish('echo', lambda x: x)
#                 except Exception as e:
#                     assert 'UNAUTHORIZED' in str(e)
                
#                 assert 'echo' not in hub.services
#                 await admin_portal.publish('echo', lambda x: x, n_workers=0)
#                 assert 'echo' in hub.services
                

#                 assert 'echo' in await admin_portal.list()
#                 assert 'echo' in await portal.list()

#                 try: # portal can't replace the service
#                     await portal.publish('echo', lambda x: x, replace=True)
#                 except Exception as e:
#                     assert 'UNAUTHORIZED' in str(e)

#                 try: # portal can't change the function signature
#                     await portal.publish('echo', lambda x=0: x)
#                 except Exception as e:
#                     assert 'UNAUTHORIZED' in str(e)

#                 class Dummy:
#                     def __init__(self, x):
#                         pass

#                 try: # portal can't change the service type
#                     await portal.publish('echo', Dummy)
#                 except Exception as e:
#                     assert 'UNAUTHORIZED' in str(e)
                
#                 # But portal can serve
#                 echo_service = await portal.publish('echo', lambda x: x, n_workers=1)

#                 assert 'x' == await echo_service('x')

#                 # Admin should be able to change authorizations
#                 await admin_portal.publish('echo', lambda x: x, n_workers=0, can_call=[['x', 0]])

#                 assert hub.services['echo']['can_call'] == [['x', 0]]
                
#                 # portal shouldn't be able to change authorizations and a warning should be emitted
#                 with pytest.warns(UserWarning) as record:
#                     await portal.publish('echo', lambda x: x, n_workers=0, can_call=[['y', 0]])

#                 assert len(record) == 1
#                 assert 'UNAUTHORIZED' in record[0].message.args[0]

#                 assert hub.services['echo']['can_call'] == [['x', 0]]

#                 # Service should still be running
#                 assert 'x' == await asyncio.wait_for(echo_service('x'), 5)
                
#                 echo_service.stop_all()
#                 service.stop_all()
