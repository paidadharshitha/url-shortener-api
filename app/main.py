"""HTTP layer, dashboard hosting, and application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.domain import (
    AliasConflict,
    CodeGenerationFailed,
    InvalidExpiry,
    Link,
    LinkExpired,
    LinkNotFound,
    LinkPaused,
)
from app.repository import LinkRepository
from app.schemas import (
    ErrorResponse,
    LinkCreate,
    LinkList,
    LinkOverview,
    LinkPatch,
    LinkResponse,
    LinkStats,
)
from app.service import Clock, LinkService, utc_now

router = APIRouter()
STATIC_DIRECTORY = Path(__file__).parent / "static"


def get_service(request: Request) -> LinkService:
    return request.app.state.link_service


ServiceDependency = Annotated[LinkService, Depends(get_service)]


def link_response(link: Link, request: Request, now: datetime) -> LinkResponse:
    return LinkResponse(
        **asdict(link),
        short_url=f"{request.app.state.settings.base_url}/{link.code}",
        is_expired=link.is_expired(now),
        status=link.status(now),
    )


@router.get("/", response_class=FileResponse, include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(
        STATIC_DIRECTORY / "index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
                "base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        },
    )


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
    link = service.create(str(payload.url), payload.alias, payload.expires_at, payload.title)
    base_url = request.app.state.settings.base_url
    response.headers["Location"] = f"{base_url}/links/{link.code}/stats"
    response.headers["Cache-Control"] = "no-store"
    return link_response(link, request, service.clock())


@router.get("/links", response_model=LinkList, tags=["Links"])
def list_links(
    request: Request,
    response: Response,
    service: ServiceDependency,
    q: Annotated[str, Query(max_length=200)] = "",
    filter_status: Annotated[
        Literal["all", "active", "paused", "expired"], Query(alias="status")
    ] = "all",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LinkList:
    """Search links by title, URL, or code, ordered newest first with stable pagination."""
    now = service.clock()
    links, total = service.list_links(q, filter_status, limit, offset, now)
    response.headers["Cache-Control"] = "no-store"
    return LinkList(
        items=[link_response(link, request, now) for link in links],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/links/overview", response_model=LinkOverview, tags=["Links"])
def link_overview(response: Response, service: ServiceDependency) -> LinkOverview:
    """Summarize link statuses, lifetime clicks, and the last seven UTC days."""
    response.headers["Cache-Control"] = "no-store"
    return LinkOverview(**service.overview())


@router.patch(
    "/links/{code}",
    response_model=LinkResponse,
    tags=["Links"],
    responses={404: {"model": ErrorResponse}},
)
def update_link(
    code: str,
    payload: LinkPatch,
    request: Request,
    response: Response,
    service: ServiceDependency,
) -> LinkResponse:
    """Edit a link. Omitted fields remain unchanged; null clears a title or expiry."""
    changes = payload.model_dump(exclude_unset=True)
    if "url" in changes:
        changes["url"] = str(changes["url"])
    link = service.update(code, changes)
    response.headers["Cache-Control"] = "no-store"
    return link_response(link, request, service.clock())


@router.get(
    "/links/{code}/stats",
    response_model=LinkStats,
    tags=["Links"],
    responses={404: {"model": ErrorResponse}},
)
def link_stats(
    code: str, request: Request, response: Response, service: ServiceDependency
) -> LinkStats:
    """Read statistics for any link status; this does not count as a click."""
    now = service.clock()
    link, daily_clicks = service.stats(code, now)
    response.headers["Cache-Control"] = "no-store"
    return LinkStats(**link_response(link, request, now).model_dump(), daily_clicks=daily_clicks)


@router.get(
    "/{code}",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    tags=["Redirects"],
    responses={404: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
)
def redirect_link(code: str, service: ServiceDependency) -> RedirectResponse:
    """Count one click and redirect only while the link is active and unexpired."""
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
        version="1.1.0",
        description="Create, manage, and track short links with optional aliases and expiry.",
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
        elif isinstance(exc, LinkPaused):
            status_code, detail = 410, f"Link '{exc.args[0]}' is paused."
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

    for error in (
        AliasConflict,
        LinkNotFound,
        LinkExpired,
        LinkPaused,
        InvalidExpiry,
        CodeGenerationFailed,
    ):
        application.add_exception_handler(error, domain_error_handler)

    application.mount(
        "/assets", StaticFiles(directory=STATIC_DIRECTORY, check_dir=False), name="assets"
    )
    application.include_router(router)
    return application


app = create_app()
