"""CLI diagnostic for the shared OpenAI API key loader."""

from __future__ import annotations

from extract.env import resolve_openai_api_key


def main() -> None:
    """Print the resolved dotenv path, key source, and masked prefix."""
    try:
        info = resolve_openai_api_key()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(f".env path: {info.env_path}")
    print(f"OPENAI_API_KEY source: {info.source}")
    print(f"OPENAI_API_KEY masked: {info.masked}")


if __name__ == "__main__":
    main()
