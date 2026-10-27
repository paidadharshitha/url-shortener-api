"""Private QR downloads and bounded CSV import/export for a user's workspace."""

import csv
from dataclasses import asdict
from datetime import datetime
from io import BytesIO, StringIO
from typing import Annotated, Literal
from urllib.parse import quote

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError
from qrcode.exceptions import DataOverflowError
from qrcode.image.svg import SvgPathImage
from starlette.concurrency import run_in_threadpool

from app.auth import User, require_user
from app.domain import AliasConflict, CodeGenerationFailed, InvalidExpiry, Link
from app.schemas import LinkCreate, LinkResponse

router = APIRouter(tags=["Transfers"])
AuthenticatedUser = Annotated[User, Depends(require_user)]
MAX_CSV_BYTES = 512 * 1024
MAX_IMPORT_ROWS = 100
MAX_EXPORT_ROWS = 10_000
CSV_COLUMNS = (
    "code",
    "title",
    "url",
    "short_url",
    "status",
    "total_clicks",
    "last_clicked_at",
    "created_at",
    "expires_at",
)


class ImportRowResult(BaseModel):
    row: int
    status: int
    link: LinkResponse | None = None
    error: str | None = None


class ImportResult(BaseModel):
    created_count: int
    error_count: int
    results: list[ImportRowResult]


def response_for_link(link: Link, base_url: str, now: datetime) -> LinkResponse:
    return LinkResponse(
        **asdict(link),
        short_url=f"{base_url}/{link.code}",
        is_expired=link.is_expired(now),
        status=link.status(now),
    )


def spreadsheet_cell(value: object) -> str:
    """Keep user-controlled cells from executing as spreadsheet formulas."""
    text = str(value) if value is not None else ""
    leading_whitespace = text[: len(text) - len(text.lstrip())]
    if text.lstrip().startswith(("=", "+", "-", "@")) or any(
        character in leading_whitespace for character in "\t\r\n"
    ):
        return "'" + text
    return text


@router.get(
    "/links/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, 413: {"description": "Export too large"}},
)
def export_links(
    request: Request,
    user: AuthenticatedUser,
    q: Annotated[str, Query(max_length=200)] = "",
    filter_status: Annotated[
        Literal["all", "active", "paused", "expired"], Query(alias="status")
    ] = "all",
) -> Response:
    """Download matching owned links, up to 10,000 rows, without recording clicks."""
    request.app.state.rate_limiter.check("management", str(user.id), 240)
    service = request.app.state.link_service
    now = service.clock()
    links, total = service.list_links(
        q, filter_status, MAX_EXPORT_ROWS + 1, 0, now, owner_id=user.id
    )
    if total > MAX_EXPORT_ROWS:
        raise HTTPException(
            413,
            "This export exceeds 10,000 links. Narrow your search or status filter and retry.",
        )
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    base_url = request.app.state.settings.base_url
    for link in links:
        values = response_for_link(link, base_url, now).model_dump(mode="json")
        writer.writerow(spreadsheet_cell(values[column]) for column in CSV_COLUMNS)
    return Response(
        output.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="shortlink-export.csv"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/links/{code}/qr.svg",
    response_class=Response,
    responses={200: {"content": {"image/svg+xml": {}}}},
)
def download_qr(code: str, request: Request, user: AuthenticatedUser) -> Response:
    """Download a locally generated QR code containing the owned link's short URL."""
    request.app.state.rate_limiter.check("management", str(user.id), 240)
    link = request.app.state.link_service.get(code, owner_id=user.id)
    short_url = f"{request.app.state.settings.base_url}/{link.code}"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(short_url)
    try:
        qr.make(fit=True)
    except DataOverflowError as exc:
        raise HTTPException(422, "The configured short URL is too long for a QR code.") from exc
    output = BytesIO()
    qr.make_image(image_factory=SvgPathImage).save(output)
    filename = quote(link.code, safe="") + ".svg"
    return Response(
        output.getvalue(),
        media_type="image/svg+xml",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


def parse_import(content: bytes) -> list[tuple[int, dict[str, str | None]]]:
    """Validate the entire CSV's structure before any link is created."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            400, "CSV must use UTF-8 encoding. Save it as CSV UTF-8 and retry."
        ) from exc
    if "\x00" in text:
        raise HTTPException(400, "CSV must be text and cannot contain null bytes.")
    reader = csv.reader(StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if not header:
            raise HTTPException(400, "CSV is empty. Include a url header and at least one link.")
        columns = [column.strip().lower() for column in header]
        allowed = {"url", "title", "alias", "expires_at"}
        if len(set(columns)) != len(columns):
            raise HTTPException(400, "CSV headers must be unique; a column is repeated.")
        if "url" not in columns or not set(columns) <= allowed:
            raise HTTPException(
                400, "CSV requires url; optional columns are title, alias, and expires_at."
            )
        rows: list[tuple[int, dict[str, str | None]]] = []
        previous_line = reader.line_num
        for values in reader:
            row_number = previous_line + 1
            previous_line = reader.line_num
            if not values or not any(value.strip() for value in values):
                continue
            if len(values) != len(columns):
                raise HTTPException(
                    400,
                    f"CSV row {row_number} has {len(values)} fields; expected {len(columns)}. "
                    "Quote values containing commas.",
                )
            row = {
                column: (value.strip() or None) if column != "url" else value.strip()
                for column, value in zip(columns, values, strict=True)
            }
            rows.append((row_number, row))
            if len(rows) > MAX_IMPORT_ROWS:
                raise HTTPException(
                    413, "Import at most 100 links at a time. Split the CSV and retry."
                )
    except csv.Error as exc:
        raise HTTPException(
            400,
            f"CSV could not be read near line {reader.line_num}. Check quoting and field sizes.",
        ) from exc
    if not rows:
        raise HTTPException(400, "CSV contains no links. Add at least one row after the header.")
    return rows


def import_rows(
    rows: list[tuple[int, dict[str, str | None]]], request: Request, user: User
) -> ImportResult:
    limiter = request.app.state.rate_limiter
    # Bulk and individual creation share the same allowance, charged before writing.
    limiter.check("create", str(user.id), 120, cost=len(rows))
    service = request.app.state.link_service
    base_url = request.app.state.settings.base_url
    results = []
    for row_number, row in rows:
        try:
            payload = LinkCreate.model_validate(row)
            link = service.create(
                str(payload.url),
                payload.alias,
                payload.expires_at,
                payload.title,
                owner_id=user.id,
            )
            results.append(
                ImportRowResult(
                    row=row_number,
                    status=201,
                    link=response_for_link(link, base_url, service.clock()),
                )
            )
        except ValidationError as exc:
            message = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_input=False, include_url=False)
            )
            results.append(ImportRowResult(row=row_number, status=422, error=message))
        except InvalidExpiry as exc:
            results.append(ImportRowResult(row=row_number, status=422, error=str(exc)))
        except AliasConflict as exc:
            results.append(
                ImportRowResult(
                    row=row_number,
                    status=409,
                    error=f"Alias '{exc.args[0]}' is already taken. Choose another alias.",
                )
            )
        except CodeGenerationFailed as exc:
            results.append(ImportRowResult(row=row_number, status=503, error=str(exc)))
    created_count = sum(result.status == 201 for result in results)
    return ImportResult(
        created_count=created_count,
        error_count=len(results) - created_count,
        results=results,
    )


@router.post(
    "/links/bulk",
    response_model=ImportResult,
    response_model_exclude_none=True,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
async def import_csv(request: Request, user: AuthenticatedUser) -> ImportResult:
    """Import up to 100 CSV rows. Valid rows succeed; each invalid row reports its error."""
    await run_in_threadpool(request.app.state.rate_limiter.check, "bulk", str(user.id), 5)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/csv":
        raise HTTPException(
            415, "Send the CSV file as the request body with Content-Type: text/csv."
        )
    length_header = request.headers.get("content-length")
    if length_header is not None:
        try:
            declared_size = int(length_header)
        except ValueError as exc:
            raise HTTPException(400, "Content-Length must be a valid byte count.") from exc
        if declared_size < 0:
            raise HTTPException(400, "Content-Length must be a valid byte count.")
        if declared_size > MAX_CSV_BYTES:
            raise HTTPException(413, "CSV must be 512 KiB or smaller. Split the file and retry.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_CSV_BYTES:
            raise HTTPException(413, "CSV must be 512 KiB or smaller. Split the file and retry.")
        body.extend(chunk)
    rows = parse_import(bytes(body))
    return await run_in_threadpool(import_rows, rows, request, user)
