import { Connection, Session, Channel } from "./index";
import { spawn } from 'child_process';

let subprocess: any;
const startBroker = `
import telekinesis as tk
import asyncio

async def main():
    broker = await tk.Broker().serve(port=8777)
    lock = asyncio.Event()
    await lock.wait()

asyncio.run(main())
`

beforeAll(() => new Promise((resolve, reject) => {
  // jest.setTimeout(300000);
  subprocess = spawn('python', ['-c', startBroker])

  subprocess.stderr.on('data', (data: any) => {
    throw `${data}`;
  });
  setTimeout(() => resolve(true), 800)
}))
afterAll(() => {
  subprocess.kill()
})

describe("Connection", () => {
  it("connects", async () => {
    const c = new Connection(new Session(), 'ws://localhost:8777');
    await c.connect()
    expect(c.brokerId).toBeTruthy();
  });
});