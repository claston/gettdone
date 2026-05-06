from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "a" and attrs_dict.get("href"):
            self.refs.append(("href", attrs_dict["href"]))
        if tag in {"script", "img", "source"} and attrs_dict.get("src"):
            self.refs.append(("src", attrs_dict["src"]))
        if tag == "link" and attrs_dict.get("href"):
            self.refs.append(("href", attrs_dict["href"]))


def iter_html_files() -> list[Path]:
    return sorted(path for path in FRONTEND_ROOT.rglob("*.html") if path.is_file())


def is_external_ref(raw_url: str) -> bool:
    value = raw_url.strip()
    if not value:
        return True
    if value.startswith("#"):
        return True
    if value.startswith(("mailto:", "tel:", "javascript:")):
        return True
    parsed = urlparse(value)
    return bool(parsed.scheme) or value.startswith("//")


def resolve_local_target(html_file: Path, raw_url: str) -> Path:
    parsed = urlparse(raw_url)
    path_part = parsed.path
    if not path_part:
        # query-only URLs target current file
        return html_file

    if path_part.startswith("/"):
        candidate = FRONTEND_ROOT / path_part.lstrip("/")
    else:
        candidate = (html_file.parent / path_part).resolve()

    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate


def check_html_references() -> list[str]:
    errors: list[str] = []
    for html_file in iter_html_files():
        content = html_file.read_text(encoding="utf-8")
        parser = LinkCollector()
        parser.feed(content)
        for ref_kind, raw_url in parser.refs:
            if is_external_ref(raw_url):
                continue
            target = resolve_local_target(html_file, raw_url)
            if not target.exists():
                rel_html = html_file.relative_to(REPO_ROOT)
                rel_target = str(target).replace(str(REPO_ROOT), "").lstrip("\\/")
                errors.append(f"{rel_html}: invalid {ref_kind} '{raw_url}' -> missing '{rel_target}'")
    return errors


def check_shared_topbar_paths() -> list[str]:
    errors: list[str] = []
    script_path = FRONTEND_ROOT / "ofx-landing.js"
    content = script_path.read_text(encoding="utf-8")

    required_abs = {
        'setAttribute("href", "/client-area.html")': "logged-in top CTA must point to absolute /client-area.html",
        'setAttribute("href", "/login.html?next=%2Fofx-convert.html")': "login link must be absolute",
        'setAttribute("href", "/ofx-convert.html")': "logged-out primary CTA must be absolute",
    }
    forbidden_rel = (
        './client-area.html',
        './login.html?next=%2Fofx-convert.html',
        './ofx-convert.html',
    )

    for snippet, message in required_abs.items():
        if snippet not in content:
            errors.append(f"frontend/ofx-landing.js: missing rule: {message}")
    for token in forbidden_rel:
        if token in content:
            errors.append(f"frontend/ofx-landing.js: forbidden relative auth path found: {token}")
    return errors


def main() -> int:
    errors = [*check_html_references(), *check_shared_topbar_paths()]
    if errors:
        print("Frontend navigation lint failed.")
        for item in errors:
            print(f"- {item}")
        return 1
    print("Frontend navigation lint passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
