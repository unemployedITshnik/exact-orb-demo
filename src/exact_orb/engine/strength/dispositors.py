"""Dispositor chains and mutual receptions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging

from .types import DispositorChain, MutualReception


LOGGER = logging.getLogger(__name__)
TRADITIONAL_SIGN_RULERS: dict[int, str] = {
    0: "mars",
    1: "venus",
    2: "mercury",
    3: "moon",
    4: "sun",
    5: "mercury",
    6: "venus",
    7: "mars",
    8: "jupiter",
    9: "saturn",
    10: "saturn",
    11: "jupiter",
}


def dispositor_for_sign(
    sign_index: int,
    ruler_map: Mapping[int, str] | None = None,
) -> str:
    """Return the traditional ruler for a sign index."""

    rulers = ruler_map or TRADITIONAL_SIGN_RULERS
    return rulers[sign_index % 12]


def calculate_dispositor_chains(
    body_signs: Mapping[str, int],
    *,
    bodies: Sequence[str] | None = None,
    ruler_map: Mapping[int, str] | None = None,
) -> tuple[dict[str, DispositorChain], tuple[MutualReception, ...]]:
    """Build finite dispositor chains with cycle detection."""

    selected = tuple(bodies or body_signs.keys())
    chains = {
        body: _chain_for_body(body, body_signs, ruler_map or TRADITIONAL_SIGN_RULERS)
        for body in selected
        if body in body_signs
    }
    receptions = _mutual_receptions(chains.values())
    LOGGER.debug(
        "calculate_dispositor_chains bodies=%d chains=%d mutual_receptions=%d",
        len(selected),
        len(chains),
        len(receptions),
    )
    return chains, receptions


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
