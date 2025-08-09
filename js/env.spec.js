const { TestEnvironment } = require('jest-environment-jsdom');

module.exports = class CustomTestEnvironment extends TestEnvironment {
  async setup() {
    await super.setup();
    const { TextEncoder, TextDecoder } = require('util');
    this.global.TextEncoder = TextEncoder;
    this.global.TextDecoder = TextDecoder;
    const fetch = await import('node-fetch');
    this.global.fetch = fetch.default;
    // if (typeof this.global.TextEncoder === 'undefined') {
    //   // const { TextEncoder, TextDecoder } = require('util');
    //   // this.global.TextEncoder = TextEncoder;
    //   // this.global.TextDecoder = TextDecoder;
    // }
  }
};
// module.exports = Environment;