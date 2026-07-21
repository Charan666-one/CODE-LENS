"""Ingestion failures, named so the API layer can map them to useful messages.

CP-1.1's gate requires that oversized or hung repositories fail *gracefully*.
A caller should never have to parse a stderr blob to find out what went wrong.
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base for every ingestion failure."""


class InvalidSourceError(IngestionError):
    """The source is not a supported, safe repository reference."""


class CloneFailedError(IngestionError):
    """git rejected the clone (bad URL, private repo, network failure)."""


class CloneTimeoutError(IngestionError):
    """The clone exceeded CLONE_TIMEOUT_SECONDS and was killed."""


class RepoTooLargeError(IngestionError):
    """The working tree exceeds MAX_REPO_SIZE_MB."""
