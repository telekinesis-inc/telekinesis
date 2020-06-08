from camarere import Node
from datetime import datetime
import asyncio

LANDING = ''
with open('landing.html', 'r') as f:
    LANDING = f.read()

def log_enter(*args, **kwargs):
    with open('../../page_enter.log', 'a') as f:
        f.write(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S\n'))

async def main():
    client = await Node(auth_file_path='root.pem', key_password=True).connect()
    await (await client.publish_service('', log_enter, LANDING)).run()

asyncio.run(main())
