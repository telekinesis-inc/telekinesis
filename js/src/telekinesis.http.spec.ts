import { spawn } from 'child_process';
import { Connection, Entrypoint, Session, Telekinesis, injectFirstArg, blockArgEvaluation } from "./index";

let subprocess: any;
const URL = 'http://localhost';
const PORT = 18778;
const HOST = `${URL}:${PORT}`;

const startBroker = `
import telekinesis as tk
import asyncio

class Registry(dict):
    pass

measures = {"count": 0, "size_kb": 0}

class MeasuredBroker(tk.Broker):
    async def handle_send(self, connection, message, source, destination):
        measures["count"] += 1
        measures["size_kb"] += len(message) / 2 ** 10
        return await super().handle_send(connection, message, source, destination)

async def main():
    broker = await MeasuredBroker().serve('${URL}', ${PORT})
    reg = Registry()
    reg['echo'] = lambda x: x
    reg['get_measures'] = lambda: measures

    class Counter:
        def __init__(self, initial_value=0):
            self.value = initial_value
        def increment(self, amount=1):
            self.value += amount
            return self

    reg['counter_python'] = Counter()

    async def call_all(lst):
        await asyncio.gather(*[l(i)._execute() for i, l in enumerate(lst)])

    reg['call_all'] = call_all

    broker.entrypoint, _ = await tk.create_entrypoint(reg, '${HOST}')
    lock = asyncio.Event()
    await lock.wait()

asyncio.run(main())
`

beforeAll(() => new Promise((resolve, _) => {
  jest.setTimeout(300000);
  subprocess = spawn('/Users/n/.miniconda3/envs/py311/bin/python', ['-c', startBroker])

  subprocess.stderr.on('data', (data: any) => {
    console.warn(`Python stderr: ${data}`);
  });
  subprocess.stdout.on('data', (data: any) => {
    console.log(`Python stdout: ${data}`);
  });
  setTimeout(() => resolve(true), 2000) // Wait 2 seconds for HTTP server to start
}))
afterAll(() => {
  subprocess.kill()
})

describe("Session (HTTP/FakeWebSocket)", () => {
  it("saves and loads private keys", async () => {
    const session = new Session();
    const sessionKey = await session.sessionKey.exportKey();
    const session2 = new Session(sessionKey);

    expect(await session.sessionKey.publicSerial()).toEqual(await session2.sessionKey.publicSerial())
  });
});

describe("Connection (HTTP/FakeWebSocket)", () => {
  it("connects", async () => {
    const c = new Connection(new Session(), HOST);
    await c.connect()
    expect(c.brokerId).toBeTruthy();
    // Clean up connection
    if (c.websocket && typeof c.websocket.close === 'function') {
      c.websocket.close();
    }
  });
});

describe("Telekinesis (HTTP/FakeWebSocket)", () => {
  it("echos", async () => {
    const echo = await (new Entrypoint(HOST) as any).get('echo');
    expect(await echo('hello!')).toEqual('hello!');
  });
  
  it("throws errors (as client)", async () => {
    const counterPython = await (new Entrypoint(HOST) as any).get('counter_python') as any;
    let ee = new Error();
    await counterPython.increment('x').catch((e: any) => {ee = e})
    expect(ee.message).toContain('unsupported operand type(s) for +=')
  });

  it('handles large messages', async () => {
    const echo = await (new Entrypoint(HOST) as any).get('echo');
    const largeMessage = Array(100000).fill(() => Math.random().toString(36).slice(3)).reduce((p, c) => p + c(), "")
    expect(await echo(largeMessage)).toEqual(largeMessage);
  });

  it('sends telekinesis objects', async () => {
    const echo = await (new Entrypoint(HOST) as any).get('echo');
    const func = (x: number) => x + 1;
    expect(await echo(func)(1)).toEqual(2);
  });

  it('manipulates remote objects', async () => {
    const registry = await new Entrypoint(HOST) as any;
    await registry.update({ test: 123 });
    expect(await registry.get('test')).toEqual(123);
  });
});