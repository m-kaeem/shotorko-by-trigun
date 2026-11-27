"""Requirement 3 hook called by src/plan.py (`_simulation`) on POST /api/plan/simulate.

Thin adapter over src/simulation.py with the signature Req 1 expects; returns one
simulation dict per ward (contract fields plus extras: crew mix, low/high band,
cost of waiting a week, per-week breakdown).
"""
from __future__ import annotations

from . import simulation as _sim


def _with_types(engine, date, deployments, scenario) -> dict:
    """A plain crew count per ward gets the crew type that prevents more cases in that ward."""
    ints = {w: int(n) for w, n in deployments.items() if not isinstance(n, dict) and int(n) > 0}
    if not ints:
        return deployments
    k_max = min(max(ints.values()), _sim.MAX_CREWS_PER_WARD)
    t = _sim._table(engine, date, scenario, k_max)
    out = dict(deployments)
    for w, n in ints.items():
        k = min(n, k_max)
        if w in t[("larvicide", "central", k)]:
            best = max(_sim.TYPES, key=lambda tp: (t[(tp, "central", k)][w], tp == "larvicide"))
            out[w] = {"larvicide": n if best == "larvicide" else 0, "indoor": n if best == "indoor" else 0}
    return out


def simulate(engine, date, h, deployments, scenario=None) -> dict:
    """{ward_id: {crews_deployed, cases_prevented, beds_saved, saline_saved, roi_cases_per_crew, ...}}.

    `cases_prevented` covers the whole 2-4 week window (summed over horizons 2, 3, 4),
    so it is the same whichever `h` the plan shows; `h` is echoed back as `plan_h`."""
    dep = _with_types(engine, date, deployments or {}, scenario)
    out = _sim.simulate(engine, date, dep, scenario)
    for s in out.values():
        s["plan_h"] = int(h)
    return out
