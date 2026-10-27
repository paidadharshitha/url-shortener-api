from html.parser import HTMLParser

from fastapi.testclient import TestClient


class DashboardAssets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: set[str] = set()
        self.scripts: set[str] = set()
        self.stylesheets: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script":
            source = attributes.get("src")
            if source and source.startswith("/assets/"):
                self.urls.add(source)
                self.scripts.add(source)
        if tag == "link":
            source = attributes.get("href")
            if source and source.startswith("/assets/"):
                self.urls.add(source)
                if attributes.get("rel") == "stylesheet":
                    self.stylesheets.add(source)


def test_dashboard_and_all_referenced_assets_are_served(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<main" in response.text
    assert "no-store" in response.headers["cache-control"]
    assets = DashboardAssets()
    assets.feed(response.text)
    assert assets.scripts
    assert assets.stylesheets

    for source in assets.urls:
        asset = client.get(source)
        assert asset.status_code == 200, source
        assert asset.content, source
        content_type = asset.headers["content-type"].split(";")[0]
        if source in assets.stylesheets:
            assert content_type == "text/css"
        if source in assets.scripts:
            assert content_type in {"application/javascript", "text/javascript"}

    assert client.get("/links/overview").json()["total_clicks"] == 0


def test_missing_dashboard_asset_returns_not_found(client: TestClient) -> None:
    assert client.get("/assets/missing-file.js").status_code == 404


def test_dashboard_security_policy_restricts_executable_content_and_framing(
    client: TestClient,
) -> None:
    response = client.get("/")
    directives = {}
    for directive in response.headers["content-security-policy"].split(";"):
        name, *sources = directive.split()
        directives[name] = sources

    for directive in ("default-src", "script-src", "style-src", "font-src", "connect-src"):
        assert directives[directive] == ["'self'"]
    for directive in ("base-uri", "object-src", "frame-ancestors"):
        assert directives[directive] == ["'none'"]
    assert directives["form-action"] == ["'self'"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
