"""Application-specific exceptions."""


class CsvReadError(Exception):
    """Raised when an uploaded CSV cannot be decoded or parsed.

    Mapped to HTTP 400 by the handler registered in ``app.main``.
    """
