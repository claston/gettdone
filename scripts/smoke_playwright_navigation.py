from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = REPO_ROOT / "frontend"


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http_ready(url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:  # noqa: S310 - local ephemeral server
                if 200 <= response.status < 500:
                    return
        except Exception:
            time.sleep(0.15)
    raise RuntimeError(f"Timed out waiting for local server: {url}")


def assert_logged_out_links(base_url: str, route: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(base_url=base_url)
        page.goto(route, wait_until="domcontentloaded")
        page.wait_for_selector("#top-auth-primary-link")

        login_href = page.get_attribute("#top-auth-login-link", "href")
        primary_href = page.get_attribute("#top-auth-primary-link", "href")

        assert login_href == "/login.html?next=%2Fofx-convert.html", (
            f"{route}: unexpected logged-out login href: {login_href!r}"
        )
        assert primary_href == "/ofx-convert.html", f"{route}: unexpected logged-out primary href: {primary_href!r}"
        browser.close()


def assert_logged_in_links(base_url: str, route: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(base_url=base_url)
        context.add_init_script(
            """
            localStorage.setItem("ofxsimples_user_token", "smoke-token");
            localStorage.setItem("ofxsimples_profile_hint", "qa@ofxsimples.test");
            """
        )
        page = context.new_page()
        page.goto(route, wait_until="domcontentloaded")
        page.wait_for_selector("#top-auth-primary-link")
        primary_href = page.get_attribute("#top-auth-primary-link", "href")
        assert primary_href == "/client-area.html", f"{route}: unexpected logged-in primary href: {primary_href!r}"
        browser.close()


def main() -> int:
    port = pick_free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port)],
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_http_ready(f"{base_url}/")
        routes = ("/blog/", "/blog/o-que-e-ofx-e-como-usar/")
        for route in routes:
            assert_logged_out_links(base_url, route)
            assert_logged_in_links(base_url, route)
        print("Playwright navigation smoke passed.")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
