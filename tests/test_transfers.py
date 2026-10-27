import csv
from io import StringIO
from xml.etree import ElementTree

import pytest
import qrcode
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.transfers import MAX_CSV_BYTES, spreadsheet_cell


@pytest.fixture
def transfer_client(tmp_path, clock):
    settings = Settings(
        database_path=tmp_path / "transfers.sqlite3",
        base_url="http://testserver",
        rate_limit_enabled=False,
    )
    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
        response = client.post(
            "/auth/register", json={"username": "transfer-owner", "password": "test-password-123"}
        )
        assert response.status_code == 201, response.text
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        yield client


def make_link(client, alias="transfer-link", **changes):
    payload = {"url": "https://example.com/destination", "alias": alias, **changes}
    response = client.post("/links", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, content):
    return client.post("/links/bulk", content=content, headers={"Content-Type": "text/csv"})


def exported_rows(response):
    assert response.status_code == 200, response.text
    return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"), newline="")))


def test_qr_download_encodes_short_url_and_does_not_record_click(transfer_client, monkeypatch):
    make_link(transfer_client, "qr-link")
    contents = []
    original_add_data = qrcode.QRCode.add_data

    def capture_data(self, data, *args, **kwargs):
        contents.append(data)
        return original_add_data(self, data, *args, **kwargs)

    monkeypatch.setattr(qrcode.QRCode, "add_data", capture_data)
    response = transfer_client.get("/links/qr-link/qr.svg")

    assert response.status_code == 200
    assert contents == ["http://testserver/qr-link"]
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["content-disposition"] == 'attachment; filename="qr-link.svg"'
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    svg = ElementTree.fromstring(response.content)
    assert svg.tag == "{http://www.w3.org/2000/svg}svg"
    assert svg.find("{http://www.w3.org/2000/svg}path") is not None
    assert transfer_client.get("/links/qr-link/stats").json()["total_clicks"] == 0


def test_qr_remains_available_for_paused_links(transfer_client):
    make_link(transfer_client)
    assert (
        transfer_client.patch("/links/transfer-link", json={"is_active": False}).status_code == 200
    )
    assert transfer_client.get("/links/transfer-link/qr.svg").status_code == 200
    assert transfer_client.get("/links/missing-link/qr.svg").status_code == 404


def test_export_handles_unicode_quotes_commas_and_newlines(transfer_client):
    title = 'Café, "launch"\n第二行'
    make_link(transfer_client, title=title)
    assert transfer_client.get("/transfer-link").status_code == 302
    response = transfer_client.get("/links/export.csv")
    rows = exported_rows(response)

    assert response.content.startswith(b"\xef\xbb\xbf")
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert len(rows) == 1
    assert rows[0]["title"] == title
    assert rows[0]["short_url"] == "http://testserver/transfer-link"
    assert rows[0]["total_clicks"] == "1"
    assert rows[0]["last_clicked_at"]
    assert transfer_client.get("/links/transfer-link/stats").json()["total_clicks"] == 1


@pytest.mark.parametrize(
    "value", ["=2+2", "+SUM(A1)", "-2+3", "@SUM(A1)", "  =1", "\ttext", "\rtext"]
)
def test_export_cells_neutralize_spreadsheet_formulas(value):
    assert spreadsheet_cell(value) == "'" + value


def test_export_neutralizes_a_formula_title_from_the_database(transfer_client):
    make_link(transfer_client, title='=HYPERLINK("https://example.com")')
    rows = exported_rows(transfer_client.get("/links/export.csv"))
    assert rows[0]["title"].startswith("'=HYPERLINK")


def test_export_filters_and_does_not_silently_stop_at_one_page(transfer_client):
    service = transfer_client.app.state.link_service
    owner_id = transfer_client.get("/auth/me").json()["user"]["id"]
    for index in range(105):
        service.create(
            "https://example.com/", f"export-{index}", None, "Campaign", owner_id=owner_id
        )
    transfer_client.patch("/links/export-0", json={"is_active": False})
    rows = exported_rows(transfer_client.get("/links/export.csv"))
    assert len(rows) == 105
    filtered = exported_rows(
        transfer_client.get("/links/export.csv", params={"q": "campaign", "status": "paused"})
    )
    assert [row["code"] for row in filtered] == ["export-0"]


def test_export_refuses_oversized_result_instead_of_truncating(transfer_client, monkeypatch):
    monkeypatch.setattr(
        transfer_client.app.state.link_service, "list_links", lambda *args, **kwargs: ([], 10_001)
    )
    response = transfer_client.get("/links/export.csv")
    assert response.status_code == 413
    assert "Narrow" in response.json()["detail"]


def test_empty_export_contains_headers(transfer_client):
    response = transfer_client.get("/links/export.csv")
    assert exported_rows(response) == []
    assert "short_url" in response.content.decode("utf-8-sig").splitlines()[0]


def test_bulk_import_returns_each_success_and_validation_or_conflict_error(transfer_client):
    make_link(transfer_client, "taken-link")
    response = upload(
        transfer_client,
        "url,title,alias,expires_at\n"
        'https://example.org/article,"Café, launch",imported-link,\n'
        "javascript:alert(1),Unsafe,invalid-link,\n"
        "https://example.org/two,Duplicate,taken-link,\n"
        "https://example.org/old,Expired,old-link,2020-01-01T00:00:00Z\n"
        "https://example.org/final,Final,,\n",
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["created_count"] == 2
    assert result["error_count"] == 3
    assert [row["row"] for row in result["results"]] == [2, 3, 4, 5, 6]
    assert [row["status"] for row in result["results"]] == [201, 422, 409, 422, 201]
    assert result["results"][0]["link"]["title"] == "Café, launch"
    assert "error" not in result["results"][0]
    assert "already taken" in result["results"][2]["error"]
    assert transfer_client.get("/links").json()["total"] == 3


def test_bulk_import_preserves_physical_row_numbers_with_quoted_newlines(transfer_client):
    response = upload(
        transfer_client,
        'url,title\nhttps://example.com/one,"First\nsecond"\n\nhttps://example.com/two,Second\n',
    )
    assert response.status_code == 200
    assert [result["row"] for result in response.json()["results"]] == [2, 5]


def test_bulk_accepts_utf8_bom_and_case_insensitive_headers(transfer_client):
    response = upload(transfer_client, b"\xef\xbb\xbfURL, Title\nhttps://example.com/,Example\n")
    assert response.status_code == 200
    assert response.json()["created_count"] == 1


@pytest.mark.parametrize(
    "body",
    [
        "",
        "url\n",
        "title\nExample\n",
        "url,URL\nhttps://example.com/,https://example.org/\n",
        "url,unknown\nhttps://example.com/,value\n",
        "url,title\nhttps://example.com/one,Valid\nhttps://example.com/two,too,many\n",
        'url,title\nhttps://example.com/one,Valid\nhttps://example.com/two,"unterminated\n',
        b"url\nhttps://example.com/\xff\n",
        b"url\nhttps://example.com/\x00\n",
    ],
)
def test_bad_csv_structure_or_encoding_creates_no_links(transfer_client, body):
    response = upload(transfer_client, body)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]
    assert transfer_client.get("/links").json()["total"] == 0


def test_bulk_size_and_row_limits_are_enforced_before_creating_links(transfer_client):
    too_many = "url\n" + "https://example.com/\n" * 101
    assert upload(transfer_client, too_many).status_code == 413
    assert upload(transfer_client, b"x" * (MAX_CSV_BYTES + 1)).status_code == 413
    assert transfer_client.get("/links").json()["total"] == 0


def test_bulk_accepts_a_full_100_row_batch_with_rate_limits_enabled(transfer_client):
    transfer_client.app.state.rate_limiter.enabled = True
    response = upload(transfer_client, "url\n" + "https://example.com/\n" * 100)
    assert response.status_code == 200
    assert response.json()["created_count"] == 100
    assert response.json()["error_count"] == 0
    assert transfer_client.get("/links").json()["total"] == 100


def test_bulk_rejects_oversized_stream_without_content_length(transfer_client):
    def chunks():
        yield b"url\n"
        yield b"x" * MAX_CSV_BYTES

    response = transfer_client.post(
        "/links/bulk", content=chunks(), headers={"Content-Type": "text/csv"}
    )
    assert response.status_code == 413
    assert transfer_client.get("/links").json()["total"] == 0


def test_bulk_requires_csv_content_type(transfer_client):
    response = transfer_client.post("/links/bulk", json={"url": "https://example.com/"})
    assert response.status_code == 415
    assert "text/csv" in response.json()["detail"]


def test_transfer_endpoints_are_private_and_owner_scoped(transfer_client):
    first_token = transfer_client.headers["Authorization"]
    make_link(transfer_client, "alice-link")
    registration = transfer_client.post(
        "/auth/register", json={"username": "other-owner", "password": "another-password"}
    )
    assert registration.status_code == 201
    transfer_client.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"
    make_link(transfer_client, "bob-link")
    assert transfer_client.get("/links/alice-link/qr.svg").status_code == 404
    assert [row["code"] for row in exported_rows(transfer_client.get("/links/export.csv"))] == [
        "bob-link"
    ]
    assert (
        upload(transfer_client, "url,alias\nhttps://example.com/,bob-import\n").status_code == 200
    )
    transfer_client.headers["Authorization"] = first_token
    assert transfer_client.get("/links/bob-import/qr.svg").status_code == 404
    assert [row["code"] for row in exported_rows(transfer_client.get("/links/export.csv"))] == [
        "alice-link"
    ]
    transfer_client.headers.pop("Authorization")
    transfer_client.cookies.clear()
    for path in ["/links/export.csv", "/links/alice-link/qr.svg"]:
        assert transfer_client.get(path).status_code == 401
    assert upload(transfer_client, "url\nhttps://example.com/\n").status_code == 401


def test_bulk_cookie_auth_requires_csrf_token(transfer_client):
    transfer_client.headers.pop("Authorization")
    assert upload(transfer_client, "url\nhttps://example.com/\n").status_code == 403
    csrf = transfer_client.get("/auth/me").json()["csrf_token"]
    transfer_client.headers["X-CSRF-Token"] = csrf
    assert upload(transfer_client, "url\nhttps://example.com/\n").status_code == 200


def test_bulk_and_single_creation_share_an_atomic_quota(transfer_client):
    limiter = transfer_client.app.state.rate_limiter
    limiter.enabled = True
    owner_id = transfer_client.get("/auth/me").json()["user"]["id"]
    limiter.check("create", str(owner_id), 120, cost=119)
    rejected = upload(transfer_client, "url\nhttps://example.com/one\nhttps://example.com/two\n")
    assert rejected.status_code == 429
    assert int(rejected.headers["retry-after"]) > 0
    assert transfer_client.get("/links").json()["total"] == 0
    make_link(transfer_client)
    assert transfer_client.post("/links", json={"url": "https://example.com/"}).status_code == 429


def test_bulk_attempt_limit_also_covers_malformed_csv(transfer_client):
    transfer_client.app.state.rate_limiter.enabled = True
    for _ in range(5):
        assert upload(transfer_client, "bad-header\nvalue\n").status_code == 400
    rejected = upload(transfer_client, "url\nhttps://example.com/\n")
    assert rejected.status_code == 429
    assert int(rejected.headers["retry-after"]) > 0
    assert transfer_client.get("/links").json()["total"] == 0
