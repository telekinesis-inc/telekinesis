import sys
import importlib
import asyncio
import glob
import uuid
import getpass

import telekinesis

branch = sys.argv[1] if len(sys.argv) > 1 else False
# passwd = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass('Key Password: ')

processes = []
async def connect():
    for service in ['server.py'] + glob.glob('services/*.py'):
        processes.append(await asyncio.create_subprocess_exec('python', service))

def disconnect():
    [p.kill() for p in processes]
    processes.clear()

async def main():
    if branch:
        endpoint = uuid.uuid4().hex+uuid.uuid4().hex
        while True:
            print('upgrade endpoint', endpoint)
            await connect()

            node = await telekinesis.Node(auth_file_path='root.pem').connect()

            await (await node.publish(endpoint, lambda: None, 0, static_page='restarting...')).await_request()
            await node.close()

            print('restarting!!')
            await (await asyncio.create_subprocess_exec('git', 'pull', 'origin', branch)).wait()
            
            disconnect()
            importlib.reload(telekinesis)
    else:
        await connect()
        await asyncio.gather(*asyncio.all_tasks())

try:
    asyncio.run(main())
except Exception as e:
    disconnect()
    [t.cancel() for t in asyncio.all_tasks()]
    print(e)