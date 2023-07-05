import { Channel, Connection, Session } from "./client";
import { Telekinesis, PermissionError } from "./telekinesis";

export async function authenticate(urlOrEntrypoint: string | Telekinesis = 'ws://localhost:8776', sessionKey?: string,
    printCallback: ((output: any) => void) = console.log, kwargs?: any) {
  
  let entrypoint: any;
  if (typeof urlOrEntrypoint === 'string') {
    entrypoint = new Entrypoint(urlOrEntrypoint,sessionKey);
  } else {
    entrypoint = urlOrEntrypoint;
  }
  
  let pubkeyWasChecked = false;

  async function printCallbackWrapper(...args: any) {
    if (!pubkeyWasChecked){
      if (args[0] !== await entrypoint._session.sessionKey.publicSerial()) {
        throw new PermissionError("Public keys don't match")
      }
      pubkeyWasChecked = true;
      if (!printCallback) {
        return
      }
      args = ["Your session public key is:\n"+args[0], ...args.slice(1)];
    }
    await printCallback.apply(null, args);
    return printCallback
  }

  let user = await entrypoint.authenticate._call([printCallbackWrapper], kwargs);
  
  if (!pubkeyWasChecked) {
    throw new PermissionError('Session Public Key was never checked')
  }
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