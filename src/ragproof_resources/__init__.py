"""Versioned contracts and licensed data, available in source and installed wheels."""

from importlib.resources import files
import json
from pathlib import Path


def _read(group: str, name: str) -> str:
    if Path(name).name != name or not name.endswith(".json"):
        raise ValueError("Resource names must be plain JSON filenames")
    resource = files(__package__).joinpath(group, name)
    if resource.is_file():
        return resource.read_text(encoding="utf-8")
    # Editable checkouts keep one authoritative copy at the repository root.
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file():
        return (root / group / name).read_text(encoding="utf-8")
    raise FileNotFoundError(f"RAGProof package resource is missing: {group}/{name}")


def load_schema(name: str) -> dict:
    return json.loads(_read("schemas", name))


def load_cuad_fixture() -> dict:
    return json.loads(_read("fixtures", "cuad_eval_subset.json"))
