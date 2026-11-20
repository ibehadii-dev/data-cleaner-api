"""FastAPI application: lifespan, router registration and error handling."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .routers import clean, validate
from .utils.errors import CsvReadError


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


@app.exception_handler(CsvReadError)
async def csv_read_error_handler(request: Request, exc: CsvReadError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})
