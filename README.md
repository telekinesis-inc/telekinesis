# Control Objects and Functions Remotely

Telekinesis introduces a unique methodology for interacting with functions and objects residing on different machines. It is designed for environments where mutual trust is limited, offering a secure and efficient approach to remote procedure calls.

Telekinesis objects serve as 'web-pointers', enabling remote parties to interact with them by calling methods, accessing attributes, and even forwarding them to other peers. A key feature of Telekinesis is its automatic creation of Telekinesis objects for any function or object used as an argument when calling a method of another Telekinesis object. This applies to everything except built-in types such as int, float, str, etc.

Similarly, when a function returns an object or another function, a Telekinesis object is automatically created to represent it. This design enables seamless interaction and manipulation of remote resources, ushering in a new level of interconnectivity between separate systems.

## Key Features

- **Automated Telekinesis Object Creation:** Telekinesis intelligently creates web-pointer objects for non-built-in types used as arguments in remote function calls or returned by these calls, facilitating seamless interactions with remote resources.
- **Flexibility in Interaction:** Telekinesis objects act as dynamic web-pointers that can be interacted with (through method calls or attribute access) or even forwarded to other peers, providing flexibility and ease of use.
- **Limited Trust Environment Compatibility:** Designed with security in mind, Telekinesis thrives in environments where parties trust each other to a limited extent. It offers a secure way to control objects and functions remotely. Supports end-to-end encryption, authenticated connections, and authorization validations to secure your communications.
- **Enhanced Remote Procedure Calls:** Telekinesis simplifies RPCs by offering a unique approach to control remote objects and functions, greatly enhancing the client-server communication process.
- **Abstracts Communication:** Telekinesis handles all your communication logic, simplifying the development perspective and adding value to both client and service logic.
- **WebSockets Based:**: Telekinesis operates on WebSocket clients on both client and service sides, with WebSocket servers acting as message brokers. This approach provides increased symmetry and opens a wealth of possibilities.
- **Interactive Development:** Get relevant information for any function or object in your REPL using autocomplete, minimizing the need to switch between documentation and code.
- **Privacy Support:** To safeguard the integrity of your objects, Telekinesis respects privacy conventions. It blocks all access to attributes and methods of an object that begin with an underscore (_), thus enabling the implementation of private attributes and methods. Only standard dunder methods such as __call__, __init__, __add__, etc., are exempted from this restriction. This feature ensures that the internal state and functionality of your objects remain under control while still allowing for extensive interactions.

## Getting Started

This section includes examples to guide you on how to install and use Telekinesis.

### Prerequisites

- Python
- Jupyter notebook or IPython (optional but recommended)
- JavaScript

### Installation

For Python, use pip to install Telekinesis:

```bash
pip install telekinesis
```

For JavaScript, make sure to install the correct package: `telekinesis-js`, **not** ~~`telekinesis`~~:

```bash
npm install telekinesis-js
```

### Data Encoding

Telekinesis provides a simple and intuitive encoding logic to manage various data types and collections. Here's how you can use it:

```python
broker.entrypoint, _ = await tk.create_entrypoint(
  lambda x: str(type(x))
)
echo_type = await tk.Entrypoint()

print(await echo_type(1)) # >> "<class 'int'>"
print(await echo_type('hi')) # >> "<class 'str'>"
print(await echo_type(b'000')) # >> "<class 'bytes'>"
print(await echo_type(True)) # >> "<class 'bool'>"
print(await echo_type(None)) # >> "<class 'NoneType'>"

print(await echo_type([1,2,3])) # >> "<class 'list'>"
print(await echo_type(set([1,2,3])) # >> "<class 'set'>"
print(await echo_type({'a': 'A', 'b': 'B'})) # >> "<class 'dict'>"

print(await echo_type(lambda: None)) # >> "<class 'telekinesis.telekinesis.Telekinesis'>"
```

You can also see that the same logic applies to whatever a function returns:
```python
broker.entrypoint, _ = await tk.create_entrypoint(
  lambda: {'int': 10, 'str': 'asdf', 'list': [1, 2, lambda: None]}
)
return_dict = await tk.Entrypoint()

print(await return_dict()) 
# >> {'int': 10, 'str': 'asdf', 'list': [1, 2, ≈ function <lambda>...]}
```
To summarize:
* Primitives or built in types (`str`, `bytes`, `int`, `float`, `bool`, `NoneType`, `range` and `slice`) are sent directly (meaning they'll be encoded using `bson` on one side and decoded on the other)
* Collections (`dict`, `list` and `set`) are encoded recursively
* Telekinesis objects are passed directly
* Everything else is wrapped in a Telekinesis object so it can be manipulated from the other side.
  
One other thing to note is that whenever a Telekinesis object is passed back to its creator, it gets converted back to the original object it was wrapping. For example:

```python
broker.entrypoint, _ = await tk.create_entrypoint(
  lambda: lambda x: str(type(x))
)

outer_lambda = await tk.Entrypoint()

inner_lambda = await outer_lambda()

# Same as the previous echo_type example
print(await inner_lambda(123)) # "<class 'int'>
print(await inner_lambda(lambda: None)) # >> "<class 'telekinesis.telekinesis.Telekinesis'>"

# However, outer_lambda get's converted back to its original function 
print(await inner_lambda(outer_lambda)) # >> "<class 'function'>" 
# (instead of "<class telekinesis.telekinesis.Telekinesis'>")
```

### Usage

#### Python

Set up a local telekinesis network and expose a module like pandas:

```python
import telekinesis as tk
import pandas # For example

# Set up a local telekinesis network
broker = await tk.Broker().serve()

# Let's create a simple passkey_check
def passkey_check(submitted_passkey):
    PASSKEY = '<write a random passkey here>'
    if submitted_passkey == PASSKEY:
        return pandas
    raise PermissionError()

# Expose pandas module with a passkey check
broker.entrypoint, _ = await tk.create_entrypoint(passkey_check) 
```

#### JavaScript

Create and manipulate pandas DataFrames from JavaScript in a web browser:

```javascript
// Connect to the entrypoint
entrypoint = await new TK.Entrypoint()

// Get pandas sdk
pandas = await entrypoint('<your passkey>')

// Create a DataFrame
df = await pandas.DataFrame()
```

## Security Notice

Please note that Telekinesis won't stop you from sharing potentially dangerous functions or objects, and you are responsible for what you expose. However, we've implemented several security measures to secure your communications. If you find any vulnerabilities, feel free to submit them as issues on our [GitHub page](https://github.com/telekinesis-inc/telekinesis/issues). You can also the maintainer directly by (email)[elias@payperrun.com).

## Additional Features

Explore all features built into Telekinesis at our [Python tests folder](https://github.com/telekinesis-inc/telekinesis/tree/main/python/test) or the [JavaScript spec file](https://github.com/telekinesis-inc/telekinesis/blob/main/js/src/telekinesis.spec.ts).

## Contributing

We welcome contributions from the community. If you're interested in helping, please check out our [contributing guide](CONTRIBUTING.md).

## License

This project is licensed under the MIT License. See [LICENSE.md](LICENSE.md) for more details.
