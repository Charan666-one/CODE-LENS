// Shape definitions.

import { multiply } from './calculator.js';

class Shape {
  area() {
    throw new Error('not implemented');
  }
}

class Rectangle extends Shape {
  constructor(width, height) {
    super();
    this.width = width;
    this.height = height;
  }

  area() {
    return multiply(this.width, this.height);
  }
}

export { Shape, Rectangle };
