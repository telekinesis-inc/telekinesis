from telekinesis import Node
from datetime import datetime
import asyncio

def log_email(*args, **kwargs):
    print('email', args, kwargs)
    with open('../../email.log', 'a') as f:
        f.write(' '.join(str(x) for x in (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S\n'), args, kwargs)))

async def main():
    client = await Node(auth_file_path='root.pem', key_password=True).connect()
            
    await client.publish('register_email', log_email, 1, True, True, 'Thank you for registering!')

asyncio.run(main())
