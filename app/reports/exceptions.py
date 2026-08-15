class ReportError(Exception):
    """Base error for the reports domain."""


class ReportNotFound(ReportError):
    pass


class ReportNotReady(ReportError):
    """The PDF has been requested before the worker finished it."""
