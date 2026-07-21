"""Two classes in one file: `self` must resolve by the call site's own class."""


class Helper:
    """An unrelated class that also lives here."""

    def assist(self):
        """Assist."""
        return 2


class Service:
    """Calls one of its own methods."""

    def start(self):
        """Start the service."""
        return self.setup()

    def setup(self):
        """Prepare the service."""
        return 1
