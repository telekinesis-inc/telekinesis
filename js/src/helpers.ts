import { Connection, Session, Channel, Route} from "./client";
import { Telekinesis } from "./telekinesis";

export function b64encode(u8: Uint8Array) {
  return btoa(String.fromCharCode.apply(null, u8 as any));
}

export function b64decode(str: string) {
  return new Uint8Array(atob(str).split('').map(function (c) { return c.charCodeAt(0); }));
}
export function intToBytes(int: number, len: number) {
  return new Uint8Array(len).map((_, i) => int/256**(len-i-1))
}
export function bytesToInt(bytes: Uint8Array) {
  return Array.from(bytes).reduce((p, c, i) => p+c*256**(bytes.length-i-1), 0)
}

export async function authenticate(url: string, printCallback: ((output: any) => void) = console.log) {

  let user = await (await new PublicUser(url) as any).authenticate._call(printCallback, ...Array.from(arguments).slice(2,));

  if (!user) {
    throw 'Failed to authenticate';
  }
  return user;
}

export class PublicUser {
  constructor(url: string) {
    if (!/(?![\w\d]+:\/\/[\w\d.]+):[\d]+/.exec(url)) {
      let i = (/[\w\d]+:\/\/[\w\d.]+/.exec(url) as any)[0].length;
      url = url.slice(0, i) + ':8776' + url.slice(i);
    }
    return new Promise<any>(async (r) => {
      let session = new Session();
      let c = new Connection(session, url);
      await c.connect();

      r(new Telekinesis(c.entrypoint as Route, session) as any);
    })
  }
}