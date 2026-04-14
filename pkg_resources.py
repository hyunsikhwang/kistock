from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata


class DistributionNotFound(Exception):
    """setuptools.pkg_resources 호환 예외."""


@dataclass
class Distribution:
    version: str


def get_distribution(dist_name: str) -> Distribution:
    try:
        return Distribution(version=metadata.version(dist_name))
    except metadata.PackageNotFoundError as exc:
        raise DistributionNotFound(dist_name) from exc


def parse_version(version: str) -> tuple[int | str, ...]:
    parts: list[int | str] = []
    for part in version.replace("-", ".").split("."):
        parts.append(int(part) if part.isdigit() else part)
    return tuple(parts)
