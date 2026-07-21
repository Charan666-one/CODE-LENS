"""Entry point for the demo."""

from calculator import multiply


def area(width, height):
    """Return the area of a rectangle."""
    return multiply(width, height)


def main():
    """Print the area of a sample rectangle."""
    print(area(3, 4))


if __name__ == "__main__":
    main()
