"""HTTP layer, dashboard hosting, and application factory."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth import AuthService, User, require_user
from app.auth import router as auth_router
from app.config import Settings
from app.domain import (
    AliasConflict,
    CodeGenerationFailed,
    InvalidDateRange,
    InvalidExpiry,
    Link,
    LinkExpired,
    LinkNotFound,
    LinkPaused,
)
from app.rate_limit import RateLimiter
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
from app.transfers import router as transfers_router

router = APIRouter()
STATIC_DIRECTORY = Path(__file__).parent / "static"


def get_service(request: Request) -> LinkService:
    return request.app.state.link_service


ServiceDependency = Annotated[LinkService, Depends(get_service)]


def management_user(request: Request, user: Annotated[User, Depends(require_user)]) -> User:
    request.app.state.rate_limiter.check("management", str(user.id), 240)
    return user


UserDependency = Annotated[User, Depends(management_user)]


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
                "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
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
    payload: LinkCreate,
    request: Request,
    response: Response,
    service: ServiceDependency,
    user: UserDependency,
) -> LinkResponse:
    """Create a short link. Custom aliases are unique and case-sensitive."""
    request.app.state.rate_limiter.check("create", str(user.id), 120)
    link = service.create(
        str(payload.url), payload.alias, payload.expires_at, payload.title, owner_id=user.id
    )
    base_url = request.app.state.settings.base_url
    response.headers["Location"] = f"{base_url}/links/{link.code}/stats"
    response.headers["Cache-Control"] = "no-store"
    return link_response(link, request, service.clock())


@router.get("/links", response_model=LinkList, tags=["Links"])
def list_links(
    request: Request,
    response: Response,
    service: ServiceDependency,
    user: UserDependency,
    q: Annotated[str, Query(max_length=200)] = "",
    filter_status: Annotated[
        Literal["all", "active", "paused", "expired"], Query(alias="status")
    ] = "all",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LinkList:
    """Search links by title, URL, or code, ordered newest first with stable pagination."""
    now = service.clock()
    links, total = service.list_links(q, filter_status, limit, offset, now, owner_id=user.id)
    response.headers["Cache-Control"] = "no-store"
    return LinkList(
        items=[link_response(link, request, now) for link in links],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/links/overview", response_model=LinkOverview, tags=["Links"])
def link_overview(
    response: Response,
    service: ServiceDependency,
    user: UserDependency,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    start_date: date | None = None,
    end_date: date | None = None,
) -> LinkOverview:
    """Summarize your workspace over 7/30/90 days or a bounded inclusive UTC date range."""
    response.headers["Cache-Control"] = "no-store"
    return LinkOverview(
        **service.overview(owner_id=user.id, days=days, start_date=start_date, end_date=end_date)
    )


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
    user: UserDependency,
) -> LinkResponse:
    """Edit a link. Omitted fields remain unchanged; null clears a title or expiry."""
    changes = payload.model_dump(exclude_unset=True)
    if "url" in changes:
        changes["url"] = str(changes["url"])
    link = service.update(code, changes, owner_id=user.id)
    response.headers["Cache-Control"] = "no-store"
    return link_response(link, request, service.clock())


@router.get(
    "/links/{code}/stats",
    response_model=LinkStats,
    tags=["Links"],
    responses={404: {"model": ErrorResponse}},
)
def link_stats(
    code: str,
    request: Request,
    response: Response,
    service: ServiceDependency,
    user: UserDependency,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    start_date: date | None = None,
    end_date: date | None = None,
) -> LinkStats:
    """Read statistics for any link status; this does not count as a click."""
    now = service.clock()
    link, daily_clicks = service.stats(
        code, now, owner_id=user.id, days=days, start_date=start_date, end_date=end_date
    )
    response.headers["Cache-Control"] = "no-store"
    return LinkStats(
        **link_response(link, request, now).model_dump(),
        daily_clicks=daily_clicks,
        start_date=daily_clicks[0]["date"],
        end_date=daily_clicks[-1]["date"],
        period_clicks=sum(day["clicks"] for day in daily_clicks),
    )


@router.get(
    "/{code}",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    tags=["Redirects"],
    responses={404: {"model": ErrorResponse}, 410: {"model": ErrorResponse}},
)
def redirect_link(code: str, request: Request, service: ServiceDependency) -> RedirectResponse:
    """Count one click and redirect only while the link is active and unexpired."""
    request.app.state.rate_limiter.check(
        "redirect", request.client.host if request.client else "unknown", 120
    )
    link = service.resolve(code)
    return RedirectResponse(link.url, status_code=302, headers={"Cache-Control": "no-store"})


def create_app(settings: Settings | None = None, *, clock: Clock = utc_now) -> FastAPI:
    configuration = settings if settings is not None else Settings.from_env()
    repository = LinkRepository(configuration.database_path)
    auth_service = AuthService(repository, clock=clock, settings=configuration)
    rate_limiter = RateLimiter(repository, clock=clock, enabled=configuration.rate_limit_enabled)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        repository.initialize()
        auth_service.initialize()
        rate_limiter.initialize()
        yield

    application = FastAPI(
        title="Shortlink API",
        version="2.0.0",
        description="Create, manage, and track short links with optional aliases and expiry.",
        lifespan=lifespan,
    )
    application.state.settings = configuration
    application.state.link_service = LinkService(repository, clock)
    application.state.auth_service = auth_service
    application.state.rate_limiter = rate_limiter

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
        # Keep field-level messages without reflecting passwords or submitted bodies.
        errors = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
        return Response(
            content=json.dumps({"detail": errors}, ensure_ascii=True),
            status_code=422,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @application.middleware("http")
    async def private_response_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/auth/", "/links")):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

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
        elif isinstance(exc, InvalidDateRange):
            status_code, detail = 422, str(exc)
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
        InvalidDateRange,
        CodeGenerationFailed,
    ):
        application.add_exception_handler(error, domain_error_handler)

    application.mount(
        "/assets", StaticFiles(directory=STATIC_DIRECTORY, check_dir=False), name="assets"
    )
    application.include_router(auth_router)
    application.include_router(transfers_router)
    application.include_router(router)
    return application


app = create_app()
