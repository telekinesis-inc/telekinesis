import { Connection, Session, Channel, Route} from "./client.js";

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

export async function validate(loginId: string, code: string) {
  let session = new Session()
  let connection = new Connection(
    session, 
    'wss://eh-us-east1.telekinesis.cloud:8776'
  )
  await connection.connect()

  // let channel = new Channel(session, true)
  // await channel.send(connection.endpoint as Route, {
  //   pipeline: {
  //     '139784966598912': ['list', ['139785351919424', '139784967463872']],
  //     '139785351919424': ['tuple', ['139785719774512', '139785719950832']],
  //     '139785719774512': ['str', 'get'],
  //     '139785719950832': ['str', 'validate'],
  //     '139784967463872': ['tuple', ['139785717325168', '139784967373568']],
  //     '139785717325168': ['str', 'call'],
  //     '139784967373568': ['tuple', ['139785719705664', '139784966684736']],
  //     '139785719705664': ['tuple', []],
  //     '139784966684736': ['dict', {'login_id': '139784966605680', 'code': '139784966605616'}],
  //     '139784966605680': ['str', loginId],
  //     '139784966605616': ['str', code],
  //     'root': '139784966598912'
  //   }
  // })
  // let out = await channel.recv()
  // await channel.close()

  // return out
}