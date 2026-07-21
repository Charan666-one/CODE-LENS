"""Dynamic dispatch — the receiver's type is not knowable statically."""

from handlers import EmailHandler, SmsHandler


def dispatch(handler, message):
    """Call whichever handler was passed in."""
    return handler.handle(message)


def build(kind):
    """Return a handler instance."""
    if kind == "email":
        return EmailHandler()
    return SmsHandler()
