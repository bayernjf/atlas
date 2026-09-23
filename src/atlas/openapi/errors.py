class OpenApiError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UnsupportedSchema(OpenApiError):
    def __init__(self, reason: str) -> None:
        super().__init__("OPENAPI_INVALID_DOCUMENT", reason)
        self.reason = reason
