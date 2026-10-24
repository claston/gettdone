"""Prefer the runner's existing HTTPS Ubuntu archive; retain all APT fallbacks."""
import re
from pathlib import Path

ARCHIVE = "https://archive.ubuntu.com/ubuntu/"
AZURE = "http://azure.archive.ubuntu.com/ubuntu/"


def prefer_ubuntu_archive(text):
    lines = text.splitlines(keepends=True)
    if not any(line.startswith(ARCHIVE) and re.search(r"\bpriority:[12]\b", line) for line in lines):
        return text
    result = []
    for line in lines:
        if line.startswith(ARCHIVE):
            line = re.sub(r"\bpriority:2\b", "priority:1", line)
        elif line.startswith(AZURE):
            line = re.sub(r"\bpriority:1\b", "priority:2", line)
        result.append(line)
    return "".join(result)


def configure(path):
    if not path.is_file():
        return False
    previous = path.read_text(encoding="utf-8")
    updated = prefer_ubuntu_archive(previous)
    if updated == previous:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


if __name__ == "__main__":
    changed = configure(Path("/etc/apt/apt-mirrors.txt"))
    print("APT mirror priorities updated; fallbacks retained." if changed else "APT mirror configuration already suitable or unrecognized.")
