import re

from .client import Session, Connection
from .telekinesis import Telekinesis


def authenticate(url="ws://localhost:8776", session_key_file=None, print_callback=print, **kwargs):

    user = Entrypoint(url, session_key_file).authenticate(print_callback, **kwargs)

    return user


def Entrypoint(url="ws://localhost:8776", session_key_file=None, **kwargs):
    s = Session(session_key_file)

    if re.sub(r"(?![\w\d]+:\/\/[\w\d.]+):[\d]+", "", url) == url:
        i = len(re.findall(r"[\w\d]+:\/\/[\w\d.]+", url)[0])
        url = url[:i] + ":8776" + url[i:]

    c = Connection(s, url)

    async def await_entrypoint():
        await c
        return c.entrypoint

    return Telekinesis(await_entrypoint(), s, **kwargs)
