const path = require('path');

module.exports = {
  entry: './lib/webpack.js',
  mode: 'production',
  output: {
    filename: 'telekinesis.js',
    path: path.resolve(__dirname, 'dist'),
  },
}