"""Shape definitions."""

from calculator import multiply


class Shape:
    """Abstract shape."""

    def area(self):
        """Return the area of the shape."""
        raise NotImplementedError


class Rectangle(Shape):
    """A rectangle with a width and a height."""

    def __init__(self, width, height):
        self.width = width
        self.height = height

    def area(self):
        """Return width multiplied by height."""
        return multiply(self.width, self.height)
