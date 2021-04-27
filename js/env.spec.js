const Environment = require('jest-environment-jsdom');

module.exports = class CustomTestEnvironment extends Environment {
  async setup() {
    await super.setup();
    const { TextEncoder, TextDecoder } = require('util');
    this.global.TextEncoder = TextEncoder;
    this.global.TextDecoder = TextDecoder;
    // if (typeof this.global.TextEncoder === 'undefined') {
    //   // const { TextEncoder, TextDecoder } = require('util');
    //   // this.global.TextEncoder = TextEncoder;
    //   // this.global.TextDecoder = TextDecoder;
    // }
  }
};
// module.exports = Environment;