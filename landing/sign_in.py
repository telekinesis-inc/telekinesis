from camarere import Node
from datetime import datetime
import asyncio
import json

async def main():
    with open('users.pem', 'r') as f:
        users = json.load(f)

    async def sign_in(request):
        u = await request.send_input_request('Username: ')
        p = await request.send_input_request('Password', True, u)

        if u in users and p == users[u]:
            await request.send_role_extension(('', 0), (u, 0))
            return True
        return False

    client = Node(auth_file_path='root.pem', key_password=True)
    while True:
        try:
            await client.connect()
            
            service = await client.publish_service('sign_in', sign_in, can_call=[['*', 0]], inject_first_arg=True)
            
            await service.run()
        except Exception as e:
            print(e)
            await asyncio.sleep(1)
        finally:
            await client.close()

asyncio.run(main())
