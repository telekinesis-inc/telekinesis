const path = require('path');

module.exports = {
  entry: './lib/webpack.js',
  mode: 'production',
  output: {
    filename: 'telekinesis.js',
    path: path.resolve(__dirname, 'dist'),
  },
  resolve: {
    fallback: {
      // These Node.js core modules won't be used in browser (behind isNode checks)
      http: false,
      https: false,
      url: false,
    }
  },
  target: 'web',
}