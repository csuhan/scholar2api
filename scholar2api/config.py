"""Configuration: auto-load .env from the project root (no dependency).

Lookup order for every setting: real environment > .env file > default.
"""
import os
import pathlib

_LOADED = False


def load_dotenv(path: str = "") -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, optional quotes.
    Never overrides variables already present in the real environment."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    # default: <project-root>/.env, independent of the process CWD
    p = pathlib.Path(path) if path else (
        pathlib.Path(__file__).resolve().parent.parent / ".env")
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


load_dotenv()
