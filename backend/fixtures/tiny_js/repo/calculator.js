// Arithmetic helpers.

export function add(a, b) {
  return a + b;
}

export function multiply(a, b) {
  let total = 0;
  for (let i = 0; i < b; i++) {
    total = add(total, a);
  }
  return total;
}
