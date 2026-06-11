from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=404)


class AuthError(AppError):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, status_code=401)


class BadRequestError(AppError):
    def __init__(self, message: str = "Bad request"):
        super().__init__(message, status_code=400)


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})
