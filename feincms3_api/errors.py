"""
Errors of the content API's operations, mapped to HTTP responses by the
endpoints
"""


class APIError(Exception):
    status = 400

    def __init__(self, detail="", **extra):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra

    def as_json(self):
        return {"detail": self.detail, **self.extra}


class NotFoundError(APIError):
    status = 404


class ForbiddenError(APIError):
    status = 403


class ConflictError(APIError):
    status = 409


class PreconditionRequiredError(APIError):
    status = 428


class PreconditionFailedError(APIError):
    status = 412


class ValidationFailedError(APIError):
    """Every problem of a write, each with its path"""

    status = 422

    def __init__(self, errors):
        super().__init__("The data isn't valid.")
        self.errors = errors

    def as_json(self):
        return {"detail": self.detail, "errors": self.errors}


class WireError(Exception):
    """A wire value a codec can't decode"""
