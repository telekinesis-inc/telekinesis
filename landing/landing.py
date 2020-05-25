from camarere import Server
from datetime import datetime
import asyncio

async def main():
    def log_enter(*args, **kwargs):
        with open('../../page_enter.log', 'a') as f:
            f.write(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S\n'))

    LANDING = ''

    with open('landing.html', 'r') as f:
        LANDING = f.read()
    
    c = []
    while True:
        try:
            c = await Server(private_key_path='PRIVATE.pem').connect()
            await c.publish('', LANDING)
            await c.serve(log_enter, '')
        except Exception as e:
            print(e)
            await asyncio.sleep(1)
        finally:
            if c:
                await c.close()

asyncio.run(main())
