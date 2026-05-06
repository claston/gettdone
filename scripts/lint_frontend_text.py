from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"

# Common mojibake sequences observed when UTF-8 content is decoded with Latin-1/Windows-1252.
MOJIBAKE_TOKENS = (
    "Ã",
    "Â",
    "â€",
    "â€“",
    "â€”",
    "â€™",
    "â€œ",
    "â€\x9d",
    "\ufffd",  # Unicode replacement char
)

TEXT_EXTENSIONS = {".html", ".css", ".js"}


def iter_frontend_files() -> list[Path]:
    files: list[Path] = []
    for path in FRONTEND_ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS:
            files.append(path)
    return sorted(files)


def find_mojibake_lines(content: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for idx, line in enumerate(content.splitlines(), start=1):
        if any(token in line for token in MOJIBAKE_TOKENS):
            findings.append((idx, line))
    return findings


def main() -> int:
    decode_errors: list[str] = []
    mojibake_errors: list[str] = []

    for path in iter_frontend_files():
        rel_path = path.relative_to(REPO_ROOT)
        raw = path.read_bytes()

        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            decode_errors.append(f"{rel_path}: invalid UTF-8 ({exc})")
            continue

        for line_no, line in find_mojibake_lines(content):
            mojibake_errors.append(f"{rel_path}:{line_no}: {line.strip()}")

    if decode_errors or mojibake_errors:
        print("Frontend text lint failed.")
        if decode_errors:
            print("\nFiles with invalid UTF-8:")
            for item in decode_errors:
                print(f"- {item}")
        if mojibake_errors:
            print("\nPotential mojibake sequences detected:")
            for item in mojibake_errors:
                print(f"- {item}")
        return 1

    print("Frontend text lint passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
