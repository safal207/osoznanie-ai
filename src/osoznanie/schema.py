"""Generate deterministic JSON Schemas for Osoznanie Protocol records."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .models import RECORD_MODELS, ProtocolRecord

PROTOCOL_VERSION = "0.1"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_BASE_URI = f"https://osoznanie.ai/schemas/v{PROTOCOL_VERSION}"


def schema_filename(record_type: str) -> str:
    """Return the stable public filename for a protocol record type."""
    return f"{record_type.replace('_', '-')}.schema.json"


def _contract_only(value: Any) -> Any:
    """Remove display labels that do not affect JSON validation."""
    if isinstance(value, dict):
        return {
            key: _contract_only(item)
            for key, item in value.items()
            if key != "title"
        }
    if isinstance(value, list):
        return [_contract_only(item) for item in value]
    return value


def build_schema(record_type: str, model: type[ProtocolRecord]) -> dict[str, object]:
    """Build one standalone Draft 2020-12 JSON Schema document."""
    schema = _contract_only(model.model_json_schema(mode="validation"))
    schema["$schema"] = SCHEMA_DIALECT
    schema["$id"] = f"{SCHEMA_BASE_URI}/{schema_filename(record_type)}"
    schema["x-osoznanie-protocol-version"] = PROTOCOL_VERSION
    return schema


def render_schema(record_type: str, model: type[ProtocolRecord]) -> str:
    """Render a schema in canonical repository form."""
    return json.dumps(
        build_schema(record_type, model),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def schema_documents() -> dict[str, str]:
    """Return every public schema keyed by its stable filename."""
    return {
        schema_filename(record_type): render_schema(record_type, model)
        for record_type, model in RECORD_MODELS.items()
    }


def sync_schemas(output_dir: Path, *, check: bool = False) -> list[str]:
    """Write schemas or report files that differ from generated output.

    In check mode no files are changed. The return value contains missing,
    stale, or unexpected schema filenames.
    """
    expected = schema_documents()
    existing = {
        path.name: path
        for path in output_dir.glob("*.schema.json")
        if path.is_file()
    } if output_dir.exists() else {}

    problems: list[str] = []
    for filename, content in expected.items():
        path = output_dir / filename
        if check:
            if not path.exists():
                problems.append(f"missing: {filename}")
            elif path.read_text(encoding="utf-8") != content:
                problems.append(f"stale: {filename}")
            continue

        output_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    unexpected = sorted(set(existing) - set(expected))
    problems.extend(f"unexpected: {filename}" for filename in unexpected)
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Osoznanie Protocol JSON Schemas."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("schemas"),
        help="Output directory (default: schemas).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when committed schemas differ from generated output.",
    )
    args = parser.parse_args(argv)

    problems = sync_schemas(args.output, check=args.check)
    if problems:
        for problem in problems:
            print(problem)
        if args.check:
            print("Run `python -m osoznanie.schema` and commit the generated files.")
            return 1

    if not args.check:
        print(f"Generated {len(schema_documents())} schemas in {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
