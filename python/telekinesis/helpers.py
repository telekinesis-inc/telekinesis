import re

from .client import Session, Connection
from .telekinesis import Telekinesis


async def authenticate(url="ws://localhost:8776", session_key_file=None, print_callback=print, **kwargs):

    user = await (await PublicUser(url, session_key_file)).authenticate._call(print_callback, **kwargs)

    if not user:
        raise Exception("Failed to authenticate")

    return user


async def PublicUser(url="ws://localhost:8776", session_key_file=None):
    s = Session(session_key_file)

    if re.sub(r'(?![\w\d]+:\/\/[\w\d.]+):[\d]+', '', url) == url:
        i = len(re.findall(r'[\w\d]+:\/\/[\w\d.]+', url)[0])
        url = url[:i] + ':8776' + url[i:]

    c = await Connection(s, url)

    return Telekinesis(c.entrypoint, s)
