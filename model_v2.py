"""
model_v2.py
───────────
Model V.2 core engine — Layer 1 + Layer 2.
Consumes DebtPortfolio.to_model_v2_inputs() + user profile inputs.

Changes from original:
  - Zone classification unified: S_E only (F_net-based zone removed)
  - epsilon removed
  - escape_score_zone removed
  - LDER denominator fixed: I * 12 for annualised income
  - t_star_adjusted low-momentum branch stretches by x1.50
  - Layer 3 removed (debt classification absorbed into portfolio tool)
  - true_event_horizon_warning() uses Lifetime Income Commitment Ratio R
    — no peak age, no career curve assumptions, fully audit-proof
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional


# ── Layer 1: Instantaneous State ──────────────────────────────────────────────

@dataclass
class ModelV2Layer1:
    """
    Inputs:
      I              — monthly income
      E_n            — necessary expenses
      E_d            — monthly discretionary spending
      P              — total monthly debt payment  (from DebtPortfolio)
      D              — total outstanding debt       (from DebtPortfolio)
      r              — weighted avg effective annual rate (from DebtPortfolio)
      behavior_score — 0-10 (from 5-question behavioral assessment)
      E_fund         — emergency fund balance
      months_on_budget — 0-6 (last 6 months on budget)
    """
    I:                     float
    E_n:                   float
    E_d:                   float
    behavior_score:        float
    E_fund:                float
    months_on_budget:      int
    target_freedom_months: int = 60

    D: float = 0.0
    r: float = 0.0
    P: float = 0.0

    def load_portfolio(self, portfolio_inputs: dict) -> "ModelV2Layer1":
        self.D = portfolio_inputs["D"]
        self.r = portfolio_inputs["r"]
        self.P = portfolio_inputs["P"]
        return self

    # ── Three Forces ──────────────────────────────────────────────────────────

    @property
    def beta(self) -> float:
        """β = 1 + behavior_score / 10  ∈ [1.0, 2.0]"""
        # score 1–10 where 10=best (lowest drag), 1=worst (highest drag)
        return 2 - max(1.0, min(10.0, self.behavior_score)) / 10

    @property
    def F_p(self) -> float:
        """Propulsion Force: F_p = I - E_n - P"""
        return self.I - self.E_n - self.P

    @property
    def F_g(self) -> float:
        """Debt Gravity: F_g = D * (r / 12)"""
        return self.D * (self.r / 12)

    @property
    def F_d(self) -> float:
        """Spending Drag: F_d = E_d * β"""
        return self.E_d * self.beta

    @property
    def F_net(self) -> float:
        """Net Monthly Force: F_net = F_p - F_g - F_d"""
        return self.F_p - self.F_g - self.F_d

    # ── Escape Score ──────────────────────────────────────────────────────────

    @property
    def B_s(self) -> float:
        """Safety buffer bonus: B_s = min(10, E_fund / (3 * E_n) * 10)"""
        if self.E_n <= 0:
            return 0.0
        return min(10.0, (self.E_fund / (3 * self.E_n)) * 10)

    @property
    def S_E(self) -> float:
        """Escape Score: S_E = (F_net / I) * 100 + B_s"""
        if self.I <= 0:
            return -100.0
        return (self.F_net / self.I) * 100 + self.B_s

    # ── Orbital Zone (S_E only — single source of truth) ─────────────────────

    @property
    def zone(self) -> str:
        s = self.S_E
        if s > 20:     return "escape_trajectory"
        elif s >= 5:   return "marginal_escape"
        elif s >= -10: return "debt_orbit"
        else:          return "black_hole"

    ZONE_DISPLAY = {
        "escape_trajectory": "🚀 Escape Trajectory",
        "marginal_escape":   "🌕 Marginal Escape",
        "debt_orbit":        "⚠️  Debt Orbit",
        "black_hole":        "🕳️  Black Hole",
    }

    @property
    def zone_label(self) -> str:
        return self.ZONE_DISPLAY[self.zone]

    # ── Time to Debt Freedom ──────────────────────────────────────────────────

    @property
    def t_star(self) -> float:
        """t* = -ln(1 - D*r/12 / P) / (r/12). Returns inf if orbit-locked."""
        r_m = self.r / 12
        D, P = self.D, self.P
        if D <= 0:
            return 0.0
        if r_m == 0:
            return D / P if P > 0 else math.inf
        if P <= D * r_m:
            return math.inf
        return -math.log(1 - (D * r_m / P)) / r_m

    @property
    def t_star_adjusted(self) -> float:
        """t* stretched by Momentum: >=0.8 as-is, >=0.5 x1.20, <0.5 x1.50"""
        t = self.t_star
        if t == math.inf:
            return math.inf
        M = self.momentum_index
        if M >= 0.8:   return t
        elif M >= 0.5: return t * 1.20
        else:          return t * 1.50

    def t_star_display(self) -> str:
        t = self.t_star_adjusted
        if t == math.inf:
            return "∞ — ORBIT LOCKED 🔴"
        years  = int(t) // 12
        months = int(t) % 12
        parts  = []
        if years:  parts.append(f"{years} year{'s' if years != 1 else ''}")
        if months: parts.append(f"{months} month{'s' if months != 1 else ''}")
        return " ".join(parts) if parts else "< 1 month"

    # ── Escape Velocity Gap ───────────────────────────────────────────────────

    @property
    def P_star(self) -> float:
        r_m = self.r / 12
        D   = self.D
        n   = self.target_freedom_months
        if D <= 0:   return 0.0
        if r_m == 0: return D / n
        return D * r_m / (1 - (1 + r_m) ** (-n))

    @property
    def delta(self) -> float:
        return self.P_star - self.P

    def gap_message(self) -> str:
        if self.delta > 0:
            return (f"You need ฿{self.delta:,.0f} more per month "
                    f"to be debt-free in {self.target_freedom_months // 12} years.")
        return f"You are already on pace. Debt-free in {self.t_star_display()}."

    # ── Momentum Index ────────────────────────────────────────────────────────

    @property
    def momentum_index(self) -> float:
        return max(0, min(6, self.months_on_budget)) / 6

    @property
    def momentum_confidence(self) -> str:
        M = self.momentum_index
        if M >= 0.8:   return "High"
        elif M >= 0.5: return "Moderate"
        else:          return "Low"

    def momentum_display(self) -> str:
        return f"Momentum: {self.momentum_index * 100:.0f}% — {self.momentum_confidence} Confidence"

    def momentum_warning(self) -> Optional[str]:
        if self.momentum_index < 0.5:
            return "⚠️  Plan requires behavioral change first"
        return None

    # ── Full Output ───────────────────────────────────────────────────────────

    def compute(self) -> dict:
        return {
            "beta":  round(self.beta, 3),
            "F_p":   round(self.F_p, 2),
            "F_g":   round(self.F_g, 2),
            "F_d":   round(self.F_d, 2),
            "F_net": round(self.F_net, 2),
            "zone":       self.zone,
            "zone_label": self.zone_label,
            "B_s": round(self.B_s, 2),
            "S_E": round(self.S_E, 2),
            "t_star_raw":      round(self.t_star, 1) if self.t_star != math.inf else None,
            "t_star_adjusted": round(self.t_star_adjusted, 1) if self.t_star_adjusted != math.inf else None,
            "t_star_display":  self.t_star_display(),
            "orbit_locked":    self.t_star == math.inf,
            "P_star":      round(self.P_star, 2),
            "delta":       round(self.delta, 2),
            "gap_message": self.gap_message(),
            "M":                round(self.momentum_index, 3),
            "momentum_display": self.momentum_display(),
            "momentum_warning": self.momentum_warning(),
            "inputs": {
                "I": self.I, "E_n": self.E_n, "E_d": self.E_d,
                "P": self.P, "D": self.D, "r_pct": round(self.r * 100, 4),
                "behavior_score": self.behavior_score,
                "E_fund": self.E_fund,
                "months_on_budget": self.months_on_budget,
            },
        }

    def print_layer1(self):
        r = self.compute()
        print(f"\n{'='*60}")
        print(f"  MODEL V.2 — LAYER 1: INSTANTANEOUS STATE")
        print(f"{'='*60}")
        print(f"  ── Three Forces ──────────────────────────────────────")
        print(f"  Propulsion   F_p  = ฿{r['F_p']:>10,.2f}")
        print(f"  Debt Gravity F_g  = ฿{r['F_g']:>10,.2f}")
        print(f"  Spending Drag F_d = ฿{r['F_d']:>10,.2f}  (β={r['beta']})")
        print(f"  Net Force    F_net= ฿{r['F_net']:>10,.2f}")
        print(f"\n  ── Escape Score ──────────────────────────────────────")
        print(f"  S_E = {r['S_E']:+.1f}  (B_s bonus = +{r['B_s']:.1f})")
        print(f"  Zone: {r['zone_label']}")
        print(f"\n  ── Time to Debt Freedom ──────────────────────────────")
        print(f"  t* (raw)      = {r['t_star_raw']} months")
        print(f"  t* (adjusted) = {r['t_star_adjusted']} months  →  {r['t_star_display']}")
        print(f"  P*  = ฿{r['P_star']:>10,.2f} / month  (for {self.target_freedom_months}-month target)")
        print(f"  Gap = ฿{r['delta']:>+10,.2f} / month")
        print(f"  {r['gap_message']}")
        print(f"\n  ── Momentum ──────────────────────────────────────────")
        print(f"  {r['momentum_display']}")
        if r['momentum_warning']:
            print(f"  {r['momentum_warning']}")
        print(f"{'='*60}\n")


# ── Layer 2: Temporal Trajectory ─────────────────────────────────────────────

CAREER_PROFILES = {
    "physical_trade":        {"t_peak": 35, "lambda": 0.06},
    "technical_engineering": {"t_peak": 42, "lambda": 0.03},
    "management_strategy":   {"t_peak": 50, "lambda": 0.02},
    "knowledge_advisory":    {"t_peak": 55, "lambda": 0.01},
}


@dataclass
class ModelV2Layer2:
    """Requires Layer 1 instance + current_age + retirement inputs."""
    layer1:      ModelV2Layer1
    current_age: int
    career_type: str   = "technical_engineering"
    t_start:     int   = 22
    t_retire:    int   = 60
    d_rate:      float = 0.03

    @property
    def HCDF(self) -> float:
        """HCDF = (t_retire - t_current) / (t_retire - t_start)"""
        denom = self.t_retire - self.t_start
        if denom <= 0:
            return 0.0
        return max(0.0, (self.t_retire - self.current_age) / denom)

    @property
    def career_lambda(self) -> float:
        return CAREER_PROFILES.get(self.career_type, {}).get("lambda", 0.03)

    @property
    def T(self) -> int:
        return max(0, self.t_retire - self.current_age)

    @property
    def LDER(self) -> float:
        """
        LDER = D0 * e^(r*T) / Σ (I*12) * HCDF * (1-λ)^t / (1+d)^t
        """
        D0  = self.layer1.D
        r   = self.layer1.r
        I   = self.layer1.I
        T   = self.T
        lam = self.career_lambda
        d   = self.d_rate
        if T <= 0 or I <= 0:
            return math.inf if D0 > 0 else 0.0
        numerator   = D0 * math.exp(r * T)
        denominator = sum(
            (I * 12) * self.HCDF * ((1 - lam) ** t) / ((1 + d) ** t)
            for t in range(T + 1)
        )
        if denominator <= 0:
            return math.inf
        return numerator / denominator

    @property
    def lder_status(self) -> str:
        lder = self.LDER
        if lder < 0.3:   return "safe"
        elif lder < 0.7: return "warning"
        elif lder < 1.0: return "critical"
        else:            return "true_event_horizon"

    LDER_DISPLAY = {
        "safe":               "✅ Healthy — debt manageable relative to lifetime income",
        "warning":            "⚠️  Elevated — debt growing faster than income capacity",
        "critical":           "🔴 High — compounded debt exceeds single-year income multiple",
        "true_event_horizon": "🔴 Very High — compounded debt far exceeds lifetime income base",
    }

    @property
    def R(self) -> float:
        """
        Lifetime Income Commitment Ratio.
        R = (P * t*_adjusted) / (I * 12 * years_to_retirement)

        What fraction of total remaining lifetime income is already
        committed to debt repayment. No career curve assumptions —
        every number comes directly from user inputs.

        Returns math.inf if orbit-locked or no remaining income.
        """
        if self.layer1.t_star == math.inf:
            return math.inf
        remaining_income = self.layer1.I * 12 * (self.t_retire - self.current_age)
        if remaining_income <= 0:
            return math.inf
        total_debt_cost = self.layer1.P * self.layer1.t_star_adjusted
        return total_debt_cost / remaining_income

    def true_event_horizon_warning(self) -> Optional[str]:
        """
        Fires based on R — Lifetime Income Commitment Ratio.

        > 0.8  : True Event Horizon
        > 0.5  : Critical
        > 0.3  : Warning
        = inf  : Orbit-locked (t* = inf)

        Fully audit-proof: no peak age, no decay rate, no career assumptions.
        Every number is directly traceable to user inputs.
        """
        R = self.R

        if R == math.inf:
            return (
                "⚫ True Event Horizon — debt is orbit-locked. "
                "100%+ of your remaining lifetime income cannot cover this debt."
            )

        remaining_income = self.layer1.I * 12 * (self.t_retire - self.current_age)
        if remaining_income <= 0:
            return None

        if R > 0.8:
            return (
                f"⚫ True Event Horizon — {R*100:.0f}% of your remaining "
                f"lifetime income is already committed to debt."
            )
        elif R > 0.5:
            return (
                f"🔴 Critical — {R*100:.0f}% of your remaining "
                f"lifetime income is already committed to debt."
            )
        elif R > 0.3:
            return (
                f"⚠️  Warning — {R*100:.0f}% of your remaining "
                f"lifetime income is already committed to debt."
            )
        return None

    def compute(self) -> dict:
        lder_val = self.LDER
        r_val    = self.R
        return {
            "HCDF":          round(self.HCDF, 4),
            "career_type":   self.career_type,
            "career_lambda": self.career_lambda,
            "T_years":       self.T,
            "LDER":          round(lder_val, 4) if lder_val != math.inf else None,
            "lder_status":   self.lder_status,
            "lder_label":    self.LDER_DISPLAY[self.lder_status],
            "R":             round(r_val, 4) if r_val != math.inf else None,
            "R_pct":         round(r_val * 100, 1) if r_val != math.inf else None,
            "teh_warning":   self.true_event_horizon_warning(),
        }

    def print_layer2(self):
        r = self.compute()
        print(f"\n  ── LAYER 2: TEMPORAL TRAJECTORY ──────────────────────")
        print(f"  HCDF = {r['HCDF']*100:.1f}%  (earning fuel remaining)")
        print(f"  Career: {r['career_type']}  λ={r['career_lambda']}")
        print(f"  Years to retirement: {r['T_years']}")
        lder_str = f"{r['LDER']:.3f}" if r['LDER'] is not None else "∞"
        print(f"  LDER = {lder_str}  →  {r['lder_label']}")
        r_str = f"{r['R_pct']:.1f}%" if r['R_pct'] is not None else "∞"
        print(f"  R    = {r_str}  (lifetime income committed to debt)")
        if r['teh_warning']:
            print(f"\n  {r['teh_warning']}")
        print()


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    cases = [
        ("A  orbit-locked",
         dict(I=20000, E_n=18500, E_d=1500, behavior_score=2, E_fund=0, months_on_budget=1),
         dict(D=150000, r=0.18, P=1500), 30),
        ("B  on pace, debt-free at 34",
         dict(I=20000, E_n=13000, E_d=2000, behavior_score=8, E_fund=15000, months_on_budget=5),
         dict(D=150000, r=0.18, P=4000), 30),
        ("C  18yr payoff, age 38",
         dict(I=35000, E_n=20000, E_d=3000, behavior_score=6, E_fund=10000, months_on_budget=4),
         dict(D=500000, r=0.12, P=6000), 38),
        ("D  safe, age 25",
         dict(I=25000, E_n=15000, E_d=2000, behavior_score=8, E_fund=20000, months_on_budget=6),
         dict(D=50000, r=0.10, P=3000), 25),
    ]

    for label, user_inputs, portfolio, age in cases:
        print(f"\n── CASE {label} {'─'*(44-len(label))}")
        l1 = ModelV2Layer1(**user_inputs)
        l1.load_portfolio(portfolio)
        l1.print_layer1()
        l2 = ModelV2Layer2(layer1=l1, current_age=age, career_type="technical_engineering")
        l2.print_layer2()