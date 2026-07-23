// Entry point for the demo.

const { multiply } = require('./calculator');

function area(width, height) {
  return multiply(width, height);
}

function main() {
  console.log(area(3, 4));
}

if (require.main === module) {
  main();
}
