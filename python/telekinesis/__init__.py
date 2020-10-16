from .client import Session, Connection, Channel, Route
from .broker import Broker
from .telekinesis import Telekinesis, inject_first_arg, State
from .helpers import authenticate

# warnings.warn('EXPERIMENTAL package. API may change. Use only in non-critical, sand-boxed applications. Submit issues at https://github.com/eneuman/camarere')

from pkg_resources import get_distribution, DistributionNotFound

try:
    __version__ = get_distribution(__name__).version
except DistributionNotFound:
     # package is not installed
    pass

__all__ = [
    '__version__',
    'Telekinesis',
    'Broker',
    'authenticate',
    'Session',
    'Connection',
    'Channel',
    'Route',
    'inject_first_arg',
    'State'
]