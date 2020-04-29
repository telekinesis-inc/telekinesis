# Camarere

Publish API endpoints. In seconds.

![Demostration](https://s3.amazonaws.com/cmrr.es/cmrr_demo.png)

## Disclaimer

This is a Proof of Concept. Do not trust with critical/confidential data (yet).

## Installation

```
pip install camarere
```

## Usage

Python:
```
# Import the camarere client
from camarere import Cmrr

# Instantiate the client to authenticate
cmrr = Cmrr('https://cmrr.es')

# Create a new endpoint
cmrr.serve(lambda name='World': 'Hello ' + name, 'hello')

print('\nCall this from another notebook (anywhere in the world):\n',
      "cmrr.call('hello', kwargs={'name': 'stranger'}, user_id='"+cmrr.user_id+"')")
```
