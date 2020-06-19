from telekinesis import Node
from datetime import datetime
import asyncio
import json

with open('users.pem', 'r') as f:
    users = json.load(f)

async def sign_in(request):
    u = await asyncio.wait_for(request.send_input_request('Username: '), 30)
    p = await asyncio.wait_for(request.send_input_request('Password', True, u), 30)

    if u in users and p == users[u]:
        await request.send_role_extension(('', 0), (u, 0))
        return True
    return False

async def main():
    client = await Node(auth_file_path='root.pem').connect()
    await client.publish('sign_in', sign_in, 30, True, True, can_call=[['*', 0]], inject_first_arg=True)

asyncio.run(main())
