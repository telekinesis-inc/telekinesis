import random
import re

from .client import Session, Connection, Route
from .telekinesis import Telekinesis


async def authenticate(url, print_callback=print, **kwargs):
    s = Session()

    if re.sub(r'(?![\w\d]+:\/\/[\w\d.]+):[\d]+', '', url) == url:
        i = len(re.findall(r'[\w\d]+:\/\/[\w\d.]+', url)[0])
        url = url[:i] + ':8776' + url[i:]

    c = await Connection(s, url)

    entrypoint = Telekinesis(c.entrypoint, s)

    user = await entrypoint._call(print_callback, **kwargs)

    if not user:
        raise Exception("Failed to authenticate")

    return user
