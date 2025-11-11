import { Channel, Connection, Session } from "./client";
import { Telekinesis, PermissionError } from "./telekinesis";

export async function authenticate(urlOrEntrypoint: string | string[] | Telekinesis = 'ws://localhost:8776', sessionKey?: string,
    printCallback: ((output: any) => void) = console.log, kwargs?: any) {

  let entrypoint: any;
  if (typeof urlOrEntrypoint === 'string' || Array.isArray(urlOrEntrypoint)) {
    entrypoint = new Entrypoint(urlOrEntrypoint, sessionKey);
  } else {
    entrypoint = urlOrEntrypoint;
  }
  
  let pubkeyWasChecked = false;

  async function printCallbackWrapper(...args: any) {
    if (!pubkeyWasChecked){
      pubkeyWasChecked = true;
      if (args[0] !== await entrypoint._session.sessionKey.publicSerial()) {
        throw new PermissionError(`Public keys don't match`)
      }
      if (!printCallback) {
        return;
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
  constructor(url: string | string[] = 'ws://localhost:8776', sessionKey?: string, ...args: any) {
    const session = new Session(sessionKey);

    // Convert single URL to array
    const urls = typeof url === 'string' ? [url] : url;

    // Normalize URLs - add default port if not specified
    const normalizedUrls = urls.map(u => {
      if (!/(?![\w\d]+:\/\/[\w\d.]+):[\d]+/.exec(u)) {
        const match = /[\w\d]+:\/\/[\w\d.]+/.exec(u);
        if (match) {
          const i = match[0].length;
          return u.slice(0, i) + ':8776' + u.slice(i);
        }
      }
      return u;
    });

    // Create connections to all URLs and race them
    async function awaitEntrypoint() {
      const connectionPromises = normalizedUrls.map(async (u) => {
        const connection = new Connection(session, u);
        await connection.connect();
        return connection.entrypoint;
      });

      // Return first successful connection's entrypoint
      return await Promise.race(connectionPromises);
    }

    return new Telekinesis(awaitEntrypoint(), session, ...args) as any;
  }
  get() {}
}

export async function createEntrypoint(target: Object, url: string | string[] = 'ws://localhost:8776', sessionKey: string,
                                       channelKey?: string, isPublic: boolean = true, ...args: any) {
  const session = new Session(sessionKey);

  // Convert single URL to array
  const urls = typeof url === 'string' ? [url] : url;

  // Normalize URLs and create connections
  for (let u of urls) {
    if (!/(?![\w\d]+:\/\/[\w\d.]+):[\d]+/.exec(u)) {
      const match = /[\w\d]+:\/\/[\w\d.]+/.exec(u);
      if (match) {
        const i = match[0].length;
        u = u.slice(0, i) + ':8776' + u.slice(i);
      }
    }

    const connection = new Connection(session, u);
    await connection.connect();
  }

  const tk = new Telekinesis(target, session, ...args);
  tk._channel = new Channel(session, channelKey, isPublic);
  tk._channel.listen();
  tk._channel.telekinesis = tk;

  await tk._channel.execute();

  return [tk._channel.route, tk];
}