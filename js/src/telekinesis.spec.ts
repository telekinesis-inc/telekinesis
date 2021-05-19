import { spawn } from 'child_process';
import { Connection, PublicUser, Session, Telekinesis } from "./index";

let subprocess: any;
const HOST = 'ws://localhost:8777';
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
    broker = await MeasuredBroker().serve(port=${HOST.split(':')[2]})
    c = tk.Connection(tk.Session(), '${HOST}')
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
    broker.entrypoint = await tk.Telekinesis(reg, c.session)._delegate('*')
    lock = asyncio.Event()
    await lock.wait()

asyncio.run(main())
`

beforeAll(() => new Promise((resolve, reject) => {
  jest.setTimeout(300000);
  subprocess = spawn('python', ['-c', startBroker])

  subprocess.stderr.on('data', (data: any) => {
    throw `${data}`;
  });
  setTimeout(() => resolve(true), 1000)
  // resolve(true);
}))
afterAll(() => {
  subprocess.kill()
})

describe("Connection", () => {
  it("connects", async () => {
    const c = new Connection(new Session(), HOST);
    await c.connect()
    expect(c.brokerId).toBeTruthy();
  });
});
describe("Telekinesis", () => {
  it("echos", async () => {
    const echo = await (new PublicUser(HOST) as any).get('echo');
    expect(await echo('hello!')).toEqual('hello!');
  });
  it('handles large messages', async () => {
    const echo = await (new PublicUser(HOST) as any).get('echo');
    const largeMessage = Array(100000).fill(() => Math.random().toString(36).slice(3)).reduce((p, c) => p + c(), "")
    expect(await echo(largeMessage)).toEqual(largeMessage);
  });
  it('sends telekinesis objects', async () => {
    const echo = await (new PublicUser(HOST) as any).get('echo');
    const func = (x: number) => x + 1;
    expect(await echo(func)(1)).toEqual(2);
  });
  it('manipulates remote objects', async () => {
    const registry = await new PublicUser(HOST) as any;
    await registry.update({ test: 123 });
    expect(await registry.get('test')).toEqual(123);
  });
  it('updates its State when its attributes change', async () => {
    class Demo {
      x: string;
      constructor() {
        this.x = '';
      }
    }
    const d = new Demo();
    const tk = new Telekinesis(d, new Session()) as any;

    expect(tk.x._last()).toEqual(d.x);  

    d.x = 'hello';
    await tk;

    expect(tk.x._last()).toEqual(d.x);  

    d.x = 'bye';
    await tk;

    expect(tk.x._last()).toEqual(d.x);  

  })

  it('receives pull updates when it subscribes (JS object)', async () => {
    class Counter {
      value: number;
      constructor(initialValue: number = 0) {
        this.value = initialValue;
      }
      increment(amount: number = 1) {
        this.value += amount;
        return this;
      }
    }
    const server = await new PublicUser(HOST) as any;
    await server.update({ counter: new Counter() });
    const a = await (new PublicUser(HOST) as any).get('counter');
    const b = await (new PublicUser(HOST) as any).get('counter')._subscribe();

    expect(await a.increment().increment().increment(2).value).toEqual(4);
    await new Promise(r => setTimeout(()=> r(true), 10));
    expect(b.value._last()).toEqual(4);
  })
  it('receives pull updates when it subscribes (python object)', async () => {
    const a = await (new PublicUser(HOST) as any).get('counter_python');
    const b = await (new PublicUser(HOST) as any).get('counter_python')._subscribe();

    expect(await a.increment().increment().increment(2).value).toEqual(4);
    await new Promise(r => setTimeout(()=> r(true), 10));
    expect(b.value._last()).toEqual(4);
  })
  it('transfers only the difference between updates', async () => {
    class Demo {
      x0: string;
      x1: string;
      constructor() {
        this.x0 = '';
        this.x1 = ''; 
      }
    }
    const d = new Demo();

    const server = new PublicUser(HOST) as any;
    const a = new Telekinesis(d, server._session) as any;

    await server.update({'demo': a});
    const b = await (new PublicUser(HOST) as any).get('demo')._subscribe();
    await b;
    const getMeasures = await (new PublicUser(HOST) as any).get('get_measures')

    const m0 = (await getMeasures()).size_kb;
    d.x0 = Array(30000).fill(() => Math.random().toString(36).slice(3)).reduce((p, c) => p + c(), "");

    await a;

    expect(a.x0._last()).toEqual(d.x0);
    await new Promise(r => setTimeout(()=> r(true), 300));
    expect(b.x0._last()).toEqual(d.x0);
    // console.log((await getMeasures()).size_kb, m0)
    const m1 = (await getMeasures()).size_kb;

    d.x1 = Array(2).fill(() => Math.random().toString(36).slice(3)).reduce((p, c) => p + c(), "");
    await a;
    await new Promise(r => setTimeout(()=> r(true), 100));

    expect(b.x1._last()).toEqual(d.x1);
    const m2 = (await getMeasures()).size_kb;
    expect(m2-m1).toBeLessThan(0.2*(m1-m0));
    // console.log((await getMeasures()).size_kb, m0)
  })

});