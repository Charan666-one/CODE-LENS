"""`super()` resolves through the base class, not the calling one."""


class Base:
    """Base behaviour."""

    def run(self):
        """Run the base."""
        return 1


class Child(Base):
    """Extends the base."""

    def run(self):
        """Run the child, then the base."""
        return super().run()
