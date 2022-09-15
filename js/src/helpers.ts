import { Channel, Connection, Session } from "./client";
import { Telekinesis } from "./telekinesis";

export function authenticate(url: string = 'ws://localhost:8776', sessionKey?: string,
  printCallback: ((output: any) => void) = console.log, kwargs?: any) {

  let user = (new Entrypoint(url, sessionKey) as any)
    .authenticate._call([printCallback], kwargs);

  return user;
}

export class Entrypoint {
  constructor(url: string = 'ws://localhost:8776', sessionKey?: string, ...args: any) {
    if (!/(?![\w\d]+:\/\/[\w\d.]+):[\d]+/.exec(url)) {
      let i = (/[\w\d]+:\/\/[\w\d.]+/.exec(url) as any)[0].length;
      url = url.slice(0, i) + ':8776' + url.slice(i);
    }
    const session = new Session(sessionKey);
    const connection = new Connection(session, url);

    return new Telekinesis(new Promise((r: any) => connection.connect().then(() => r(connection.entrypoint))), session, ...args) as any
  }
  get() {}
}

export async function createEntrypoint(target: Object, url: string = 'ws://localhost:8776', sessionKey: string, 
                                       channelKey?: string, isPublic: boolean = true, ...args: any) {
  if (!/(?![\w\d]+:\/\/[\w\d.]+):[\d]+/.exec(url)) {
    let i = (/[\w\d]+:\/\/[\w\d.]+/.exec(url) as any)[0].length;
    url = url.slice(0, i) + ':8776' + url.slice(i);
  }
  const session = new Session(sessionKey);
  const connection = new Connection(session, url);

  await connection.connect();

  const tk = new Telekinesis(target, session, ...args);
  tk._channel = new Channel(session, channelKey, isPublic)
  tk._channel.listen();
  tk._channel.telekinesis = tk;

  await tk._channel.execute();

  return tk._channel.route, tk;
}