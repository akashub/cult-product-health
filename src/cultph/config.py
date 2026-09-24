"""Loads config.private.yaml (real tab/column names, SKU master; gitignored) or
falls back to config.example.yaml (generic names, used by tests and the public repo)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .normalize import alias_key

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


@dataclass
class Product:
    name: str
    category: str
    kind: str
    skus: list[str]
    aliases: list[str]


@dataclass
class Config:
    raw: dict
    path: Path
    products: list[Product] = field(default_factory=list)

    @property
    def source(self) -> dict:
        return self.raw["source"]

    @property
    def tabs(self) -> dict:
        return self.raw["tabs"]

    @property
    def dayfirst(self) -> bool:
        return bool(self.raw.get("dayfirst", True))

    @property
    def thresholds(self) -> dict:
        return {"min_mapping_coverage": 0.98, "max_month_label_mismatch": 0, **self.raw.get("thresholds", {})}

    @property
    def product_issue_l2(self) -> set[str]:
        return {alias_key(v) for v in self.raw.get("product_issue_l2", [])}

    @property
    def not_mentioned(self) -> set[str]:
        return {alias_key(v) for v in self.raw.get("not_mentioned", [])}

    @property
    def platform_map(self) -> dict[str, str]:
        return {alias_key(k): v for k, v in self.raw.get("platform_map", {}).items()}

    @property
    def channel_prefixes(self) -> list[tuple[str, str]]:
        return [(p.lower(), v) for p, v in self.raw.get("channel_prefixes", [])]

    @property
    def dashboard_check(self) -> dict | None:
        return self.raw.get("dashboard_check")

    def sku_index(self) -> dict[str, str]:
        return _unique_index((s, p.name) for p in self.products for s in p.skus)

    def category_of(self) -> dict[str, str]:
        return {p.name: p.category for p in self.products}

    def alias_index(self) -> dict[str, str]:
        return _unique_index((a, p.name) for p in self.products for a in [p.name, *p.aliases])


def _unique_index(pairs) -> dict[str, str]:
    """Two products must never share a lookup key, or one silently wins."""
    idx: dict[str, str] = {}
    for text, product in pairs:
        key = alias_key(text)
        if idx.get(key, product) != product:
            raise ValueError(f"config: {text!r} maps to both {idx[key]!r} and {product!r}")
        idx[key] = product
    return idx


def load_config(path: str | os.PathLike | None = None) -> Config:
    if path is None:
        env = os.environ.get("CULTPH_CONFIG")
        private = ROOT / "config.private.yaml"
        path = Path(env) if env else (private if private.exists() else ROOT / "config.example.yaml")
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    products = [
        Product(
            name=p["name"],
            category=p.get("category", "massager"),
            kind=p.get("kind", ""),
            skus=[str(s) for s in p.get("skus", [])],
            aliases=[str(a) for a in p.get("aliases", [])],
        )
        for p in raw.get("products", [])
    ]
    return Config(raw=raw, path=path, products=products)


def env(name: str) -> str | None:
    """Reads a secret from the environment, falling back to data/.env (gitignored)."""
    if os.environ.get(name):
        return os.environ[name]
    f = DATA_DIR / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None
