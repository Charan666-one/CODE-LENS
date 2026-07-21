"""Two handlers deliberately sharing a method name."""


class EmailHandler:
    """Sends email."""

    def handle(self, message):
        """Handle by email."""
        return message


class SmsHandler:
    """Sends SMS."""

    def handle(self, message):
        """Handle by SMS."""
        return message
