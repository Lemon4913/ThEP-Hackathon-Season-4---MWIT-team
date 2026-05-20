"""
nudge_engine.py — Behavioral Nudge Engine v4

4 levers only (in priority order):
  1. E_d   — discretionary spending   step = E_d × 10%  (decrease)
  2. score — behavior score            step = 1 pt       (increase)
  3. P     — monthly payment           step = P × 10%    (increase)
  4. I     — monthly income            step = I × 10%    (increase)

Single-lever feasibility:
  E_d, score, P : ≤ 2 steps to achieve zone change
  I             : ≤ 1 step  (>10% income raise is unrealistic)

Output: top 3 recommendations
  Slots filled by: feasible single-levers first (sorted by steps),
  then mix (2–4 variables, each 1 step) if slots remain.

Mix search:
  Try every combination of 2, 3, then 4 variables each at 1 step.
  Return the first (smallest combo size) that achieves zone change.
  If 1-step-each doesn't work, try allowing one variable at 2 steps.
"""
from __future__ import annotations
import math
import copy
import itertools
from dataclasses import dataclass
from typing import Optional

from model_v2 import ModelV2Layer1, ModelV2Layer2


# ── Zone helpers ──────────────────────────────────────────────────────────────

ZONE_LABELS = {
    "escape_trajectory": "🚀 Escape Trajectory",
    "marginal_escape":   "🟡 Marginal Escape",
    "debt_orbit":        "⚠️  Debt Orbit",
    "black_hole":        "🕳️  Black Hole",
}
ZONE_ORDER = ["black_hole", "debt_orbit", "marginal_escape", "escape_trajectory"]

def _zone_from_se(se: float) -> str:
    if se > 20:     return "escape_trajectory"
    elif se >= 5:   return "marginal_escape"
    elif se >= -10: return "debt_orbit"
    else:           return "black_hole"

def _zone_rank(zone: str) -> int:
    return ZONE_ORDER.index(zone) if zone in ZONE_ORDER else 0


# ── Lever definitions ─────────────────────────────────────────────────────────
#  (field, direction, step_fn, max_single_steps, short_name, unit_label)

def _lever_defs(l1: ModelV2Layer1) -> list:
    return [
        dict(field="E_d",            dir=-1, step=l1.E_d * 0.10,        max_steps=2, name="Spending cut",    unit="฿/mo"),
        dict(field="behavior_score", dir=+1, step=1.0,                   max_steps=2, name="Behavior score",  unit="pt"),
        dict(field="P",              dir=+1, step=max(l1.P * 0.10, 100), max_steps=2, name="Payment boost",   unit="฿/mo"),
        dict(field="I",              dir=+1, step=l1.I * 0.10,           max_steps=1, name="Income boost",    unit="฿/mo"),
    ]


BEHAVIOR_ACTIONS = [
    "Set up auto-payment to eliminate missed payments",
    "Apply a 24-hour pause before any purchase over ฿500",
    "Track every expense daily for 30 days",
    "Keep credit utilization below 30% of each card limit",
    "Freeze new credit applications for 6 months",
]


# ── NudgeEngine ───────────────────────────────────────────────────────────────

@dataclass
class NudgeEngine:
    layer1: ModelV2Layer1
    layer2: ModelV2Layer2
    behavior_questions: Optional[list] = None

    # ── Core simulation ───────────────────────────────────────────────────────

    def _sim(self, **overrides) -> tuple[float, float]:
        l1 = copy.copy(self.layer1)
        for k, v in overrides.items():
            setattr(l1, k, v)
        return l1.S_E, l1.t_star_adjusted

    def _apply_step(self, lev: dict, n_steps: int) -> float:
        """Return new field value after n_steps on lever lev."""
        v = getattr(self.layer1, lev["field"]) + lev["dir"] * lev["step"] * n_steps
        if lev["field"] == "E_d":            v = max(0.0, v)
        if lev["field"] == "behavior_score": v = min(10.0, max(1.0, v))
        return v

    def _deltas(self, new_SE: float, new_t: float):
        b_t = self.layer1.t_star_adjusted
        d_se = new_SE - self.layer1.S_E
        d_t  = None if (b_t == math.inf or new_t == math.inf) else b_t - new_t
        return round(d_se, 1), (round(d_t, 1) if d_t is not None else None)

    # ── Single-lever search ───────────────────────────────────────────────────

    def _single_lever(self, lev: dict) -> Optional[dict]:
        """
        Find minimum steps (1 … max_steps) where lever achieves zone change.
        Returns None if field value is 0 and step would do nothing.
        """
        base       = getattr(self.layer1, lev["field"])
        base_zone  = self.layer1.zone
        base_t     = self.layer1.t_star_adjusted

        if lev["step"] == 0:
            return None

        result = None
        for n in range(1, lev["max_steps"] + 1):
            new_val  = self._apply_step(lev, n)
            new_SE, new_t = self._sim(**{lev["field"]: new_val})
            zone_up  = _zone_rank(_zone_from_se(new_SE)) > _zone_rank(base_zone)
            unlocked = base_t == math.inf and new_t != math.inf

            if zone_up or unlocked:
                d_se, d_t = self._deltas(new_SE, new_t)
                pct = abs((new_val - base) / base * 100) if base != 0 else n * 10
                return dict(
                    type="single", lev=lev, steps=n, pct=pct,
                    new_val=new_val, new_SE=round(new_SE, 1),
                    delta_SE=d_se, delta_t=d_t,
                    new_zone=_zone_from_se(new_SE),
                    zone_upgraded=zone_up, unlocks_orbit=unlocked,
                )
        return None   # zone change not achievable within max_steps

    # ── Mix search ────────────────────────────────────────────────────────────

    def _mix(self, exclude_types: set) -> Optional[dict]:
        """
        Find minimum-variable combo (2–4 levers, each 1 step) that achieves
        zone change. Falls back to allowing one lever at 2 steps if needed.
        exclude_types: lever names already shown as singles (avoid duplication).
        """
        levers    = _lever_defs(self.layer1)
        base_zone = self.layer1.zone
        base_t    = self.layer1.t_star_adjusted

        def try_combo(combo, steps_per_lev):
            """Simulate all levers in combo at given step counts."""
            overrides = {}
            for lev, n in zip(combo, steps_per_lev):
                overrides[lev["field"]] = self._apply_step(lev, n)
            new_SE, new_t = self._sim(**overrides)
            zone_up  = _zone_rank(_zone_from_se(new_SE)) > _zone_rank(base_zone)
            unlocked = base_t == math.inf and new_t != math.inf
            return new_SE, new_t, zone_up or unlocked

        def build_mix(combo, steps_per_lev, new_SE, new_t):
            d_se, d_t = self._deltas(new_SE, new_t)
            parts = []
            for lev, n in zip(combo, steps_per_lev):
                base_v  = getattr(self.layer1, lev["field"])
                new_v   = self._apply_step(lev, n)
                pct     = abs((new_v - base_v) / base_v * 100) if base_v != 0 else n * 10
                sign    = "+" if lev["dir"] > 0 else "-"
                parts.append(dict(
                    name=lev["name"], field=lev["field"],
                    steps=n, pct=round(pct, 0),
                    base_val=base_v, new_val=new_v, sign=sign,
                ))
            total_steps = sum(steps_per_lev)
            label = "  +  ".join(
                f"{p['name']} {p['sign']}{p['pct']:.0f}%" for p in parts
            )
            return dict(
                type="mix", steps=total_steps,
                label=f"Mix ({total_steps} total steps): {label}",
                parts=parts,
                new_SE=round(new_SE, 1), delta_SE=d_se, delta_t=d_t,
                new_zone=_zone_from_se(new_SE),
                zone_upgraded=_zone_rank(_zone_from_se(new_SE)) > _zone_rank(base_zone),
                unlocks_orbit=base_t == math.inf and new_t != math.inf,
            )

        # Pass 1: every combo of 2–4 levers, each at exactly 1 step
        for size in range(2, 5):
            for combo in itertools.combinations(levers, size):
                steps_per = [1] * size
                new_SE, new_t, ok = try_combo(combo, steps_per)
                if ok:
                    return build_mix(combo, steps_per, new_SE, new_t)

        # Pass 2: 2–3 levers, allow one lever at 2 steps (still realistic)
        for size in range(2, 4):
            for combo in itertools.combinations(levers, size):
                for boost_idx in range(size):
                    steps_per = [1] * size
                    steps_per[boost_idx] = 2
                    new_SE, new_t, ok = try_combo(combo, steps_per)
                    if ok:
                        return build_mix(combo, steps_per, new_SE, new_t)

        return None   # no combination found

    # ── Main API ──────────────────────────────────────────────────────────────

    def generate(self) -> list[dict]:
        if self.layer1.zone == "escape_trajectory":
            return []

        levers = _lever_defs(self.layer1)

        # Compute single-lever results for all 4
        singles = [self._single_lever(lev) for lev in levers]
        singles = [s for s in singles if s is not None]
        singles.sort(key=lambda x: x["steps"])   # fewest steps first

        # Take top 2 single-lever results
        top = singles[:2]
        used_types = {s["lev"]["name"] for s in top}

        # Fill remaining slot(s) with mix
        remaining = 3 - len(top)
        if remaining > 0:
            mix = self._mix(used_types)
            if mix:
                top.append(mix)

        for i, n in enumerate(top, 1):
            n["rank"] = i
        return top

    # ── Printer ───────────────────────────────────────────────────────────────

    def print_report(self):
        nudges  = self.generate()
        l1      = self.layer1
        R       = self.layer2.R
        R_str   = f"{R*100:.1f}%" if R != math.inf else "∞"

        print(f"\n{'='*64}")
        print(f"  🎯  NUDGE ENGINE — ZONE-CHANGE RECOMMENDATIONS")
        print(f"{'='*64}")
        print(f"  Zone  : {l1.zone_label}")
        print(f"  S_E   : {l1.S_E:.1f}")
        print(f"  t*    : {l1.t_star_display()}")
        print(f"  β     : {l1.beta:.2f}x  (behavior {l1.behavior_score:.0f}/10)")
        print(f"  R     : {R_str}")
        teh = self.layer2.true_event_horizon_warning()
        if teh:
            print(f"\n  {teh}")
        print()

        if not nudges:
            print(f"  🚀  Already in Escape Trajectory — maintain S_E > 20.")
            print("=" * 64)
            return

        def _fmt_val(field, val):
            if field in ("I", "P", "E_d"):
                return f"฿{val:,.0f}/mo"
            if field == "behavior_score":
                return f"{val:.0f}/10"
            return f"{val:.1f}"

        for n in nudges:
            se_sign = f"+{n['delta_SE']:.1f}" if n["delta_SE"] >= 0 else f"{n['delta_SE']:.1f}"
            is_mix  = n["type"] == "mix"

            if is_mix:
                print(f"  ── #{n['rank']}  🔀  {n['label']}")
                for p in n["parts"]:
                    base_str = _fmt_val(p["field"], p["base_val"])
                    new_str  = _fmt_val(p["field"], p["new_val"])
                    print(f"       {p['name']:20s}  {base_str} → {new_str}  ({p['sign']}{p['pct']:.0f}%)")
            else:
                lev      = n["lev"]
                base_val = getattr(l1, lev["field"])
                step_str = f"{n['steps']} step{'s' if n['steps']>1 else ''}"
                print(f"  ── #{n['rank']}  {lev['name']}  {n['pct']:.0f}%  [{step_str}]")
                print(f"       {_fmt_val(lev['field'], base_val)} → {_fmt_val(lev['field'], n['new_val'])}")

                # Behavior hint
                if lev["field"] == "behavior_score" and self.behavior_questions:
                    for i, q in enumerate(self.behavior_questions[:5]):
                        if q < 2:
                            print(f"       Habit: {BEHAVIOR_ACTIONS[i]}")
                            break

                # Spending drag detail
                if lev["field"] == "E_d":
                    drag_saved = (l1.E_d - n["new_val"]) * l1.beta
                    print(f"       Actual drag saved (×β {l1.beta:.2f}): ฿{drag_saved:,.0f}/mo")

            print(f"       ΔS_E : {se_sign} pts  →  {l1.S_E:.1f} → {n['new_SE']:.1f}")

            dt = n.get("delta_t")
            if n.get("unlocks_orbit"):
                print(f"       Δt*  : 🔓 orbit broken")
            elif dt is not None:
                if abs(dt) < 0.5:
                    print(f"       Δt*  : no change")
                elif dt > 0:
                    print(f"       Δt*  : {abs(dt):.0f} months faster")
                else:
                    print(f"       Δt*  : {abs(dt):.0f} months longer (trade-off)")
            else:
                print(f"       Δt*  : orbit still locked")

            if n.get("zone_upgraded"):
                print(f"       ✨ Zone → {ZONE_LABELS.get(n['new_zone'], n['new_zone'])}")
            print()

        print("=" * 64)


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    cases = [
        ("A  orbit-locked",
         dict(I=20000, E_n=18500, E_d=1500, behavior_score=2,
              E_fund=0, months_on_budget=1),
         dict(D=150000, r=0.18, P=1500), 30, [0, 2, 1, 2, 1]),

        ("B  on pace, debt-free at 34",
         dict(I=20000, E_n=13000, E_d=2000, behavior_score=8,
              E_fund=15000, months_on_budget=5),
         dict(D=150000, r=0.18, P=4000), 30, [2, 0, 0, 1, 1]),

        ("C  18yr payoff, age 38",
         dict(I=35000, E_n=20000, E_d=3000, behavior_score=6,
              E_fund=10000, months_on_budget=4),
         dict(D=500000, r=0.12, P=6000), 38, [1, 1, 0, 2, 2]),

        ("D  safe, age 25",
         dict(I=25000, E_n=15000, E_d=2000, behavior_score=8,
              E_fund=20000, months_on_budget=6),
         dict(D=50000, r=0.10, P=3000), 25, [2, 0, 2, 1, 2]),
    ]

    for label, u, portfolio, age, bqs in cases:
        print(f"\n{'─'*64}")
        print(f"  CASE {label}")
        print(f"{'─'*64}")
        l1 = ModelV2Layer1(**u)
        l1.load_portfolio(portfolio)
        l2 = ModelV2Layer2(layer1=l1, current_age=age,
                           career_type="technical_engineering")
        NudgeEngine(layer1=l1, layer2=l2, behavior_questions=bqs).print_report()