from telekinesis import Portal
from datetime import datetime
from markdown2 import Markdown
import asyncio

LANDING = ''
CSS = ''
with open('services/landing.html', 'r') as f:
    LANDING = f.read()
with open('services/landing.css', 'r') as f:
    CSS = f.read().replace('.highlight', '.codehilite')

with open('../../README.md', 'r') as f:
    raw = f.read()
    md = Markdown(extras=['fenced-code-blocks'])
    readme = md.convert('\n'.join(raw.split('\n')[3:-2]+[
        'Check out the [Telekinesis Github repo](https://github.com/e-neuman/telekinesis/)',
    ]))
    LANDING = LANDING % readme

def log_enter(*args, **kwargs):
    with open('page_enter.log', 'a') as f:
        f.write(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S\n'))

async def main():
    client = await Portal(auth_file_path='root.pem').connect()
    await client.publish('landing.css', lambda: None, 0, False, True, CSS)
    await client.publish('', log_enter, 1, True, True, LANDING)

asyncio.run(main())
