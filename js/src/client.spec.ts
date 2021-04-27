import { Connection, Session, Channel } from "./index";
import { spawn } from 'child_process';

let subprocess: any;
const startBroker = `
import telekinesis as tk
import asyncio

async def main():
    #broker = await tk.Broker().serve()
    lock = asyncio.Event()
    await lock.wait()

asyncio.run(main())
`

beforeAll(() => {
  subprocess = spawn('python', ['-c', startBroker])

  subprocess.stderr.on('data', (data: any) => {
    throw `${data}`;
  });
})
afterAll(() => {
  subprocess.kill()
})

describe("Connection", () => {
  it("connects", async () => {
    const c = new Connection(new Session());
    await c.connect()
    expect(c.brokerId).toBeTruthy();
  });
});