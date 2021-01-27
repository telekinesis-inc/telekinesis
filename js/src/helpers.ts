import { Connection, Session, Channel, Route} from "./client";
import { Telekinesis } from "./telekinesis";

const btoa_ = typeof btoa !== 'undefined' ? btoa : require('btoa');
const atob_ = typeof atob !== 'undefined' ? atob : require('atob');

export function b64encode(u8: Uint8Array) {
  return btoa_(String.fromCharCode.apply(null, u8 as any));
}
export function b64decode(str: string) {
  return new Uint8Array(atob_(str).split('').map(function (c: string) { return c.charCodeAt(0); }));
}
export function intToBytes(int: number, len: number) {
  return new Uint8Array(len).map((_, i) => int/256**(len-i-1))
}
export function bytesToInt(bytes: Uint8Array) {
  return Array.from(bytes).reduce((p, c, i) => p+c*256**(bytes.length-i-1), 0)
}

export async function authenticate(url: string= 'ws://127.0.0.1:8776', sessionKey?: {privateKey: {}, publicKey: {}}, 
  printCallback: ((output: any) => void) = console.log) {

  let user = await (await new PublicUser(url, sessionKey) as any)
    .authenticate._call(printCallback, ...Array.from(arguments).slice(3,));

  if (!user) {
    throw 'Failed to authenticate';
  }
  return user;
}

export class PublicUser {
  constructor(url: string = 'ws://127.0.0.1:8776', sessionKey?: {privateKey: {}, publicKey: {}}) {
    if (!/(?![\w\d]+:\/\/[\w\d.]+):[\d]+/.exec(url)) {
      let i = (/[\w\d]+:\/\/[\w\d.]+/.exec(url) as any)[0].length;
      url = url.slice(0, i) + ':8776' + url.slice(i);
    }
    return new Promise<any>(async (r) => {
      let session = new Session(sessionKey);
      let c = new Connection(session, url);
      await c.connect();

      r(new Telekinesis(c.entrypoint as Route, session) as any);
    })
  }
}