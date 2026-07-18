"""Shared repository-root environment loading for TenderSentry commands."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path


LOGGER = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
_SOURCE_LOGGED = False


@dataclass(frozen=True)
class OpenAIKeyInfo:
    """Describe the resolved API key without changing how callers consume it."""

    value: str
    source: str
    env_path: Path

    @property
    def masked(self) -> str:
        """Return only the first six key characters followed by a mask."""
        return f"{self.value[:6]}..."


def load_env_file(env_path: Path | None = None) -> set[str]:
    """Load unset variables from a dotenv file and return their names."""
    path = (env_path or ENV_PATH).resolve()
    if not path.is_file():
        return set()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        LOGGER.warning("Could not read %s: %s", path, exc)
        return set()

    loaded: set[str] = set()
    content_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        content_lines.append(stripped)
        match = re.fullmatch(
            r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)",
            stripped,
        )
        if match is None:
            continue
        key, raw_value = match.groups()
        value = _parse_env_value(raw_value)
        if not os.environ.get(key, "").strip():
            os.environ[key] = value
            loaded.add(key)

    is_single_bare_value = len(content_lines) == 1 and "=" not in content_lines[0]
    bare_value = _parse_env_value(content_lines[0]) if is_single_bare_value else ""
    if (
        "OPENAI_API_KEY" not in loaded
        and not os.environ.get("OPENAI_API_KEY", "").strip()
        and is_single_bare_value
        and bare_value.startswith("sk-")
    ):
        os.environ["OPENAI_API_KEY"] = bare_value
        loaded.add("OPENAI_API_KEY")
        LOGGER.warning(
            "%s contains a bare API key; prefer OPENAI_API_KEY=<key>", path
        )
    return loaded


def resolve_openai_api_key(env_path: Path | None = None) -> OpenAIKeyInfo:
    """Resolve the API key from the environment first, then repository .env."""
    path = (env_path or ENV_PATH).resolve()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    source = "environment"
    if not api_key:
        loaded = load_env_file(path)
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if "OPENAI_API_KEY" in loaded:
            source = ".env"
    if not api_key:
        raise RuntimeError(
            f"OPENAI_API_KEY is missing; set it in the environment or {path}"
        )
    return OpenAIKeyInfo(value=api_key, source=source, env_path=path)


def require_openai_api_key(env_path: Path | None = None) -> str:
    """Return the API key and log its source with a safely masked prefix."""
    global _SOURCE_LOGGED

    info = resolve_openai_api_key(env_path)
    if not _SOURCE_LOGGED:
        LOGGER.info(
            "OPENAI_API_KEY found in %s: %s (path: %s)",
            info.source,
            info.masked,
            info.env_path,
        )
        _SOURCE_LOGGED = True
    return info.value


def _parse_env_value(raw_value: str) -> str:
    """Parse a quoted or unquoted dotenv value without exposing it in logs."""
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"}:
        quote = value[0]
        closing_quote = value.find(quote, 1)
        if closing_quote != -1:
            remainder = value[closing_quote + 1 :].strip()
            if not remainder or remainder.startswith("#"):
                return value[1:closing_quote]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()
