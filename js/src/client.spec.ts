import { Connection, Session, Channel } from "./index";
// TODO
describe("Connection", () => {
  it("connects", () => {
    expect(await (new Connection(new Session(), 'ws://localhost:8776').connect())).toEqual(void);
  });
});