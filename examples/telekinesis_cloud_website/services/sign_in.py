from telekinesis import Node
from datetime import datetime
import asyncio
import json
import os
import re

if os.path.exists('users.pem'):
    with open('users.pem', 'r') as f:
        users = json.load(f)
else:
    users = {}

async def sign_up(request):
    await request.send_update(print='This is a demo, the user you create might get deleted')
    while True:
        await request.send_update(print='Username is case insensitive, allowed characters are letters, numbers and underscore _, min length 5')
        u = str(await asyncio.wait_for(request.send_input_request('Username: '), 30)).lower()

        if u in users:
            await request.send_update(print='Username already in use. Choose another.')
            continue

        if not re.sub(r'[a-z0-9\_]+', '', u) and len(u) >= 5:
            break
    while True:
        await request.send_update(print='This is a demo, DO NOT TRUST THIS, do not re-use a password')
        p = await asyncio.wait_for(request.send_input_request('Password', True, u), 30)
        p2 = await asyncio.wait_for(request.send_input_request('Confirm password', True, u), 30)
        if p == p2:
            break

    users[u] = p
    with open('users.pem', 'w') as f:
        json.dump(users, f)
    
    await request.send_role_extension(('', 0), (u, 0))
    return 'Welcome ' + u

async def sign_in(request):
    u = await asyncio.wait_for(request.send_input_request('Username: '), 30)
    p = await asyncio.wait_for(request.send_input_request('Password', True, u), 30)

    if u in users and p == users[u]:
        await request.send_role_extension(('', 0), (u, 0))
        return True
    return False

async def main():
    client = await Node(auth_file_path='root.pem').connect()
    await client.publish('sign_up', sign_up, 20, False, True, can_call=[['*', 0]], inject_first_arg=True)
    await client.publish('sign_in', sign_in, 20, True, True, can_call=[['*', 0]], inject_first_arg=True)

asyncio.run(main())
