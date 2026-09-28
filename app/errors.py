class AppError(Exception):
    """Domain error rendered as {"detail": ..., "code": ...}."""

    def __init__(self, status_code: int, code: str, detail: str, headers: dict | None = None):
        self.status_code, self.code, self.detail, self.headers = status_code, code, detail, headers
        super().__init__(detail)


def not_found(detail: str, code: str = "not_found") -> AppError:
    return AppError(404, code, detail)


def conflict(detail: str, code: str = "conflict") -> AppError:
    return AppError(409, code, detail)


def forbidden(detail: str = "You do not have permission to do this") -> AppError:
    return AppError(403, "forbidden", detail)


def unauthorized(detail: str = "Authentication required", code: str = "not_authenticated") -> AppError:
    return AppError(401, code, detail, {"WWW-Authenticate": "Bearer"})
