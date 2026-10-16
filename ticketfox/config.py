"""Loaders for config/ and fixtures/. No schema layer: the yaml/json files are the schema."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _yaml(name):
    return yaml.safe_load((ROOT / "config" / name).read_text())


def load_customers():
    return {c["name"]: c for c in _yaml("customers.yaml")["customers"]}


def load_modes():
    return _yaml("modes.yaml")


def load_tickets():
    return json.loads((ROOT / "fixtures" / "tickets.json").read_text())


def load_kb():
    return [(p.stem, p.read_text()) for p in sorted((ROOT / "fixtures" / "kb").glob("*.md"))]


def spent_so_far():
    """Estimated $ already spent by tagged runs and evals (rows with a run_tag), summed over results/*.jsonl."""
    rows = (json.loads(ln) for f in (ROOT / "results").glob("*.jsonl") for ln in f.read_text().splitlines())
    return sum(r["est_cost"] for r in rows if r.get("run_tag") and r["kind"] in ("call", "eval"))
