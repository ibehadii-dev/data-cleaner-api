"""FastAPI application: lifespan, router registration and error handling."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .routers import clean, validate
from .services.csv_cleaner import MAX_FILE_BYTES
from .utils.errors import CsvReadError

# Best-effort network-level guards. Starlette normally enforces Content-Length
# before the handler runs when a proxy forwards it; this middleware does the
# same check inside the app. A client may omit the header, so the streaming
# guards in the routers remain necessary.
MAX_VALIDATE_BODY_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = MAX_FILE_BYTES + 1024 * 1024  # multipart overhead


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup hook (reserved for future resource initialisation).
    yield
    # Shutdown hook.


app = FastAPI(
    title="Data Cleaning Microservice",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(clean.router)
app.include_router(validate.router)


@app.middleware("http")
async def enforce_body_size_limit(request: Request, call_next):
    """Reject oversized requests early using the Content-Length header."""
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            size = int(raw_length)
        except ValueError:
            size = 0
        limit = (
            MAX_VALIDATE_BODY_BYTES
            if request.url.path == "/validate/json"
            else MAX_UPLOAD_REQUEST_BYTES
        )
        if size > limit:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": (
                        "Request body exceeds the "
                        f"{limit // (1024 * 1024)} MB limit."
                    )
                },
            )
    return await call_next(request)


@app.exception_handler(CsvReadError)
async def csv_read_error_handler(request: Request, exc: CsvReadError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})
