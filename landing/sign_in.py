from camarere import Node
from datetime import datetime
import asyncio
import json

with open('users.pem', 'r') as f:
    users = json.load(f)

async def sign_in(request):
    u = await asyncio.wait_for(request.send_input_request('Username: '), 5)
    p = await asyncio.wait_for(request.send_input_request('Password', True, u), 5)

    if u in users and p == users[u]:
        await request.send_role_extension(('', 0), (u, 0))
        return True
    return False

async def main():
    client = await Node(auth_file_path='root.pem', key_password=True).connect()
    service = await client.publish_service('sign_in', sign_in, can_call=[['*', 0]], inject_first_arg=True)

    lst = [asyncio.create_task(service.run()) for _ in range(30)]
    await asyncio.gather(*lst)

asyncio.run(main())
