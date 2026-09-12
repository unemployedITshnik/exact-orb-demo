"""Dispositor chains and mutual receptions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
from typing import Literal

from exact_orb.engine.ephemeris.types import MODERN_RULERS, TRADITIONAL_RULERS

from .types import DispositorChain, MutualReception


LOGGER = logging.getLogger(__name__)
DispositorSystem = Literal["traditional", "modern"]


def dispositor_for_sign(
    sign_index: int,
    ruler_map: Mapping[int, str],
) -> str:
    """Return the explicitly configured single ruler for a sign index."""

    return ruler_map[sign_index % 12]


def calculate_dispositor_chains(
    body_signs: Mapping[str, int],
    *,
    bodies: Sequence[str] | None = None,
    system: DispositorSystem | None = None,
    ruler_map: Mapping[int, str] | None = None,
) -> tuple[dict[str, DispositorChain], tuple[MutualReception, ...]]:
    """Build finite dispositor chains with cycle detection."""

    rulers = _resolve_ruler_map(system=system, ruler_map=ruler_map)
    selected = tuple(bodies or body_signs.keys())
    chains = {
        body: _chain_for_body(body, body_signs, rulers)
        for body in selected
        if body in body_signs
    }
    receptions = _mutual_receptions(chains.values())
    LOGGER.debug(
        "calculate_dispositor_chains system=%s bodies=%d chains=%d mutual_receptions=%d",
        system or "custom",
        len(selected),
        len(chains),
        len(receptions),
    )
    return chains, receptions


def _resolve_ruler_map(
    *,
    system: DispositorSystem | None,
    ruler_map: Mapping[int, str] | None,
) -> Mapping[int, str]:
    if system is None and ruler_map is None:
        raise ValueError("dispositor calculation requires system or ruler_map")
    if system is not None and ruler_map is not None:
        raise ValueError("dispositor calculation accepts either system or ruler_map, not both")
    if ruler_map is not None:
        return ruler_map

    if system == "traditional":
        table = TRADITIONAL_RULERS
    elif system == "modern":
        table = MODERN_RULERS
    else:
        raise ValueError("dispositor system must be 'traditional' or 'modern'")

    result: dict[int, str] = {}
    for sign_index, sign_rulers in enumerate(table):
        if len(sign_rulers) != 1:
            raise ValueError(
                "linear dispositor calculation requires exactly one ruler "
                f"for sign index {sign_index}"
            )
        result[sign_index] = sign_rulers[0]
    return result


def _chain_for_body(
    body: str,
    body_signs: Mapping[str, int],
    ruler_map: Mapping[int, str],
) -> DispositorChain:
    visited: dict[str, int] = {}
    chain: list[str] = []
    current = body

    while current not in visited:
        visited[current] = len(chain)
        chain.append(current)
        if current not in body_signs:
            return DispositorChain(
                body=body,
                chain=tuple(chain),
                steps_to_cycle=len(chain) - 1,
                cycle=(current,),
            )
        current = dispositor_for_sign(body_signs[current], ruler_map)

    cycle_start = visited[current]
    return DispositorChain(
        body=body,
        chain=tuple((*chain, current)),
        steps_to_cycle=cycle_start,
        cycle=tuple(chain[cycle_start:]),
    )


def _mutual_receptions(chains: Sequence[DispositorChain]) -> tuple[MutualReception, ...]:
    seen: set[frozenset[str]] = set()
    receptions: list[MutualReception] = []

    for chain in chains:
        if len(chain.cycle) != 2:
            continue
        key = frozenset(chain.cycle)
        if key in seen:
            continue
        seen.add(key)
        receptions.append(MutualReception(body_1=chain.cycle[0], body_2=chain.cycle[1]))

    return tuple(receptions)
