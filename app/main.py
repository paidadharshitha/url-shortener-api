"""HTTP layer and application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import Settings
from app.domain import AliasConflict, CodeGenerationFailed, InvalidExpiry, LinkExpired, LinkNotFound
from app.repository import LinkRepository
from app.schemas import ErrorResponse, LinkCreate, LinkResponse, LinkStats
from app.service import Clock, LinkService, utc_now

router = APIRouter()


def get_service(request: Request) -> LinkService:
    return request.app.state.link_service


ServiceDependency = Annotated[LinkService, Depends(get_service)]


@router.post(
    "/links",
    status_code=status.HTTP_201_CREATED,
    response_model=LinkResponse,
    tags=["Links"],
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def create_link(
    payload: LinkCreate, request: Request, response: Response, service: ServiceDependency
) -> LinkResponse:
    """Create a short link. Custom aliases are unique and case-sensitive."""
    link = service.create(str(payload.url), payload.alias, payload.expires_at)
    base_url = request.app.state.settings.base_url
    response.headers["Location"] = f"{base_url}/links/{link.code}/stats"
    response.headers["Cache-Control"] = "no-store"
    return LinkResponse(
        code=link.code,
        url=link.url,
        short_url=f"{base_url}/{link.code}",
        created_at=link.created_at,
        expires_at=link.expires_at,
    )


@router.get(
    "/links/{code}/stats",
    response_model=LinkStats,
    tags=["Links"],
    responses={404: {"model": ErrorResponse}},
)
def link_stats(code: str, response: Response, service: ServiceDependency) -> LinkStats:
    """Read statistics, including for expired links; this does not count as a click."""
    link = service.get(code)
    response.headers["Cache-Control"] = "no-store"
    return LinkStats(
        code=link.code,
        url=link.url,
        created_at=link.created_at,
        expires_at=link.expires_at,
        total_clicks=link.total_clicks,
        last_clicked_at=link.last_clicked_at,
        is_expired=link.is_expired(service.clock()),
    )


@router.get(
    "/{code}",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    tags=["Redirects"],
    responses={404: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
)
def redirect_link(code: str, service: ServiceDependency) -> RedirectResponse:
    """Count one click and redirect if the link exists and has not expired."""
    link = service.resolve(code)
    return RedirectResponse(link.url, status_code=302, headers={"Cache-Control": "no-store"})


def create_app(settings: Settings | None = None, *, clock: Clock = utc_now) -> FastAPI:
    configuration = settings if settings is not None else Settings.from_env()
    repository = LinkRepository(configuration.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        repository.initialize()
        yield

    application = FastAPI(
        title="Shortlink API",
        version="1.0.0",
        description="Create short links with optional aliases and expiry, then track their clicks.",
        lifespan=lifespan,
    )
    application.state.settings = configuration
    application.state.link_service = LinkService(repository, clock)

    async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, AliasConflict):
            status_code, detail = (
                409,
                f"Alias '{exc.args[0]}' is already taken. Choose another alias.",
            )
        elif isinstance(exc, LinkNotFound):
            status_code, detail = 404, f"No link found for code '{exc.args[0]}'."
        elif isinstance(exc, LinkExpired):
            status_code, detail = 410, f"Link '{exc.args[0]}' has expired."
        elif isinstance(exc, InvalidExpiry):
            status_code, detail = (
                422,
                [{"loc": ["body", "expires_at"], "msg": str(exc), "type": "value_error"}],
            )
        else:
            status_code, detail = 503, str(exc)
        return JSONResponse(
            status_code=status_code,
            content={"detail": detail},
            headers={"Cache-Control": "no-store"},
        )

    for error in (AliasConflict, LinkNotFound, LinkExpired, InvalidExpiry, CodeGenerationFailed):
        application.add_exception_handler(error, domain_error_handler)

    application.include_router(router)
    return application


app = create_app()
