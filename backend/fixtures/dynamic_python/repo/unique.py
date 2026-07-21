"""A method name unique in the repository: a defensible bet, not a proof."""


class Cache:
    """Holds things."""

    def invalidate_everything(self):
        """Drop all entries."""
        return True


def refresh(cache):
    """Refresh via a duck-typed argument."""
    return cache.invalidate_everything()
