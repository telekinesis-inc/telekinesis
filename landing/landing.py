from camarere import Node
from datetime import datetime
import asyncio

async def main():
    def log_enter(*args, **kwargs):
        with open('../../page_enter.log', 'a') as f:
            f.write(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S\n'))

    LANDING = ''

    with open('landing.html', 'r') as f:
        LANDING = f.read()
    client = Node(auth_file_path='root.pem', key_password=True)
    while True:
        try:
            await client.connect()
            
            service = await client.publish_service('', log_enter, LANDING)
            
            await service.run()
        except Exception as e:
            print(e)
            await asyncio.sleep(1)
        finally:
            await client.close()

asyncio.run(main())
