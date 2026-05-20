"""
model_v2.py
───────────
Model V.2 core engine — Layer 1 / Layer 2 / Layer 3.
Consumes DebtPortfolio.to_model_v2_inputs() + user profile inputs.

Implements every formula from the Model_2.pdf spec exactly.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional


# ── Layer 1: Instantaneous State ──────────────────────────────────────────────

@dataclass
class ModelV2Layer1:
    """
    Inputs (from user + portfolio):
      I   — monthly income
      E_n — necessary expenses (rent, food, transport, utilities)
      E_d — monthly discretionary spending
      P   — total monthly debt payment  ← from DebtPortfolio
      D   — total outstanding debt      ← from DebtPortfolio
      r   — weighted avg effective rate  ← from DebtPortfolio
      behavior_score — 0-10 slider
      E_fund — emergency fund balance
      months_on_budget — how many of last 6 months user stayed on budget
    """
    # User-supplied
    I:               float   # monthly income
    E_n:             float   # necessary expenses
    E_d:             float   # discretionary spending
    behavior_score:  float   # 0-10
    E_fund:          float   # emergency fund ฿
    months_on_budget: int    # 0-6 (last 6 months)
    target_freedom_months: int = 60  # default 5-year horizon for P*

    # From DebtPortfolio.to_model_v2_inputs()
    D:   float = 0.0
    r:   float = 0.0
    P:   float = 0.0

    def load_portfolio(self, portfolio_inputs: dict) -> "ModelV2Layer1":
        """Feed DebtPortfolio.to_model_v2_inputs() directly."""
        self.D = portfolio_inputs["D"]
        self.r = portfolio_inputs["r"]
        self.P = portfolio_inputs["P"]
        return self

    # ── Three Forces ──────────────────────────────────────────────────────────

    @property
    def beta(self) -> float:
        """β = 1 + behavior_score / 10  ∈ [1.0, 2.0]"""
        return 1 + max(0, min(10, self.behavior_score)) / 10

    @property
    def F_p(self) -> float:
        """Propulsion Force: F_p = I - E_n - P"""
        return self.I - self.E_n - self.P

    @property
    def F_g(self) -> float:
        """Debt Gravity: F_g = D * (r/12)"""
        return self.D * (self.r / 12)

    @property
    def F_d(self) -> float:
        """Spending Drag: F_d = E_d * β"""
        return self.E_d * self.beta

    @property
    def F_net(self) -> float:
        """Net Monthly Force: F_net = F_p - F_g - F_d"""
        return self.F_p - self.F_g - self.F_d

    # ── Orbital Zone Classification ───────────────────────────────────────────

    @property
    def epsilon(self) -> float:
        """Buffer threshold: ε = 0.05 * I"""
        return 0.05 * self.I

    @property
    def zone(self) -> str:
        """Orbital zone based on F_net vs ε."""
        f = self.F_net
        e = self.epsilon
        if f > e:
            return "escape_trajectory"
        elif f > 0:
            return "marginal_escape"
        elif f >= -e:
            return "debt_orbit"
        else:
            return "black_hole"

    ZONE_DISPLAY = {
        "escape_trajectory": "🚀 Escape Trajectory",
        "marginal_escape":   "🌕 Marginal Escape",
        "debt_orbit":        "⚠️  Debt Orbit",
        "black_hole":        "🕳️  Black Hole",
    }

    @property
    def zone_label(self) -> str:
        return self.ZONE_DISPLAY[self.zone]

    # ── Escape Score ──────────────────────────────────────────────────────────

    @property
    def B_s(self) -> float:
        """Safety buffer bonus: B_s = min(10, E_fund / (3*E_n) * 10)"""
        if self.E_n <= 0:
            return 0.0
        return min(10.0, (self.E_fund / (3 * self.E_n)) * 10)

    @property
    def S_E(self) -> float:
        """Escape Score: S_E = (F_net / I) * 100 + B_s"""
        if self.I <= 0:
            return -100.0
        return (self.F_net / self.I) * 100 + self.B_s

    @property
    def escape_score_zone(self) -> str:
        s = self.S_E
        if s > 20:
            return "escape_trajectory"
        elif s >= 5:
            return "marginal_escape"
        elif s >= -10:
            return "debt_orbit"
        else:
            return "black_hole"

    # ── Time to Debt Freedom ──────────────────────────────────────────────────

    @property
    def t_star(self) -> float:
        """
        t* = -ln(1 - D*r/12 / P) / (r/12)   [months]
        Returns math.inf if orbit-locked.
        """
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
        """t* adjusted by Momentum Index confidence."""
        t = self.t_star
        if t == math.inf:
            return math.inf
        M = self.momentum_index
        if M >= 0.8:
            return t              # high confidence — show as-is
        elif M >= 0.5:
            return t * 1.20       # moderate — stretch +20%
        else:
            return t              # low — show warning instead

    def t_star_display(self) -> str:
        """Format t* as '3 years 11 months'."""
        t = self.t_star_adjusted
        if t == math.inf:
            return "∞ — ORBIT LOCKED 🔴"
        years  = int(t) // 12
        months = int(t) % 12
        parts  = []
        if years:
            parts.append(f"{years} year{'s' if years != 1 else ''}")
        if months:
            parts.append(f"{months} month{'s' if months != 1 else ''}")
        return " ".join(parts) if parts else "< 1 month"

    # ── Escape Velocity Gap ───────────────────────────────────────────────────

    @property
    def P_star(self) -> float:
        """Minimum monthly payment to clear debt in target_freedom_months."""
        r_m = self.r / 12
        D   = self.D
        n   = self.target_freedom_months
        if D <= 0:
            return 0.0
        if r_m == 0:
            return D / n
        return D * r_m / (1 - (1 + r_m) ** (-n))

    @property
    def delta(self) -> float:
        """Gap Δ = P* - P.  Positive = need more. Negative = already on pace."""
        return self.P_star - self.P

    def gap_message(self) -> str:
        if self.delta > 0:
            return (f"You need ฿{self.delta:,.0f} more per month "
                    f"to be debt-free in {self.target_freedom_months//12} years.")
        else:
            return f"You are already on pace. Debt-free in {self.t_star_display()}."

    # ── Momentum Index ────────────────────────────────────────────────────────

    @property
    def momentum_index(self) -> float:
        """M = months_on_budget / 6  ∈ [0, 1]"""
        return max(0, min(6, self.months_on_budget)) / 6

    @property
    def momentum_confidence(self) -> str:
        M = self.momentum_index
        if M >= 0.8:
            return "High"
        elif M >= 0.5:
            return "Moderate"
        else:
            return "Low"

    def momentum_display(self) -> str:
        return f"Momentum: {self.momentum_index*100:.0f}% — {self.momentum_confidence} Confidence"

    def momentum_warning(self) -> Optional[str]:
        if self.momentum_index < 0.5:
            return "⚠️  Plan requires behavioral change first"
        return None

    # ── Full Layer 1 Output ───────────────────────────────────────────────────

    def compute(self) -> dict:
        return {
            # Three Forces
            "beta":          round(self.beta, 3),
            "F_p":           round(self.F_p, 2),
            "F_g":           round(self.F_g, 2),
            "F_d":           round(self.F_d, 2),
            "F_net":         round(self.F_net, 2),
            "epsilon":       round(self.epsilon, 2),

            # Zone
            "zone":          self.zone,
            "zone_label":    self.zone_label,

            # Escape Score
            "B_s":           round(self.B_s, 2),
            "S_E":           round(self.S_E, 2),
            "score_zone":    self.escape_score_zone,

            # Time to Freedom
            "t_star_raw":    round(self.t_star, 1) if self.t_star != math.inf else None,
            "t_star_display":self.t_star_display(),
            "orbit_locked":  self.t_star == math.inf,

            # Gap
            "P_star":        round(self.P_star, 2),
            "delta":         round(self.delta, 2),
            "gap_message":   self.gap_message(),

            # Momentum
            "M":             round(self.momentum_index, 3),
            "momentum_display": self.momentum_display(),
            "momentum_warning": self.momentum_warning(),

            # Raw inputs (for audit)
            "inputs": {
                "I": self.I, "E_n": self.E_n, "E_d": self.E_d,
                "P": self.P, "D": self.D, "r_pct": round(self.r*100, 4),
                "behavior_score": self.behavior_score,
                "E_fund": self.E_fund,
                "months_on_budget": self.months_on_budget,
            }
        }

    def print_layer1(self):
        r = self.compute()
        print(f"\n{'='*60}")
        print(f"  MODEL V.2 — LAYER 1: INSTANTANEOUS STATE")
        print(f"{'='*60}")
        print(f"  ── Three Forces ──────────────────────────────────────")
        print(f"  Propulsion  F_p  = ฿{r['F_p']:>10,.2f}")
        print(f"  Debt Gravity F_g = ฿{r['F_g']:>10,.2f}")
        print(f"  Spending Drag F_d= ฿{r['F_d']:>10,.2f}  (β={r['beta']})")
        print(f"  Net Force  F_net = ฿{r['F_net']:>10,.2f}  (ε=±฿{r['epsilon']:,.0f})")
        print(f"\n  Zone:  {r['zone_label']}")
        print(f"\n  ── Escape Score ──────────────────────────────────────")
        print(f"  S_E = {r['S_E']:+.1f}  (B_s bonus = +{r['B_s']:.1f})")
        print(f"\n  ── Time to Debt Freedom ──────────────────────────────")
        print(f"  t*  = {r['t_star_display']}")
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
    "physical_trade":       {"t_peak": 35, "lambda": 0.06},
    "technical_engineering":{"t_peak": 42, "lambda": 0.03},
    "management_strategy":  {"t_peak": 50, "lambda": 0.02},
    "knowledge_advisory":   {"t_peak": 55, "lambda": 0.01},
}


@dataclass
class ModelV2Layer2:
    """Requires Layer 1 output + current_age + career_type."""
    layer1:       ModelV2Layer1
    current_age:  int
    career_type:  str = "technical_engineering"  # key from CAREER_PROFILES
    t_start:      int = 22    # career start age
    t_retire:     int = 60    # planned retirement age
    d_rate:       float = 0.03  # discount rate

    @property
    def HCDF(self) -> float:
        """Human Capital Discount Factor: remaining productive fraction."""
        denom = self.t_retire - self.t_start
        if denom <= 0:
            return 0.0
        return max(0, (self.t_retire - self.current_age) / denom)

    @property
    def career_lambda(self) -> float:
        return CAREER_PROFILES.get(self.career_type, {}).get("lambda", 0.03)

    @property
    def T(self) -> int:
        """Years to retirement."""
        return max(0, self.t_retire - self.current_age)

    @property
    def LDER(self) -> float:
        """
        Lifetime Debt-to-Earning Ratio.
        LDER = D0 * e^(r*T) / Σ_{t=0}^{T} [I * HCDF * (1-λ)^t / (1+d)^t]
        """
        D0 = self.layer1.D
        r  = self.layer1.r
        I  = self.layer1.I
        T  = self.T
        lam = self.career_lambda
        d  = self.d_rate

        if T <= 0 or I <= 0:
            return math.inf if D0 > 0 else 0.0

        numerator   = D0 * math.exp(r * T)
        denominator = sum(
            I * self.HCDF * ((1 - lam) ** t) / ((1 + d) ** t)
            for t in range(T + 1)
        )
        if denominator <= 0:
            return math.inf
        return numerator / denominator

    @property
    def lder_status(self) -> str:
        lder = self.LDER
        if lder < 0.3:
            return "safe"
        elif lder < 0.7:
            return "warning"
        elif lder < 1.0:
            return "critical"
        else:
            return "true_event_horizon"

    LDER_DISPLAY = {
        "safe":               "✅ Safe — debt manageable over lifetime",
        "warning":            "⚠️  Warning — must act soon",
        "critical":           "🔴 Critical — approaching True Event Horizon",
        "true_event_horizon": "⚫ True Event Horizon — mathematically unpayable",
    }

    def true_event_horizon_warning(self) -> Optional[str]:
        if self.LDER >= 0.7:
            return (f"⚠️ True Event Horizon: Age {self.current_age} — "
                    f"your earning power decays before this debt does.")
        return None

    def compute(self) -> dict:
        return {
            "HCDF":          round(self.HCDF, 4),
            "career_lambda": self.career_lambda,
            "T_years":       self.T,
            "LDER":          round(self.LDER, 4) if self.LDER != math.inf else None,
            "lder_status":   self.lder_status,
            "lder_label":    self.LDER_DISPLAY[self.lder_status],
            "teh_warning":   self.true_event_horizon_warning(),
        }

    def print_layer2(self):
        r = self.compute()
        print(f"\n  ── LAYER 2: TEMPORAL TRAJECTORY ──────────────────────")
        print(f"  HCDF = {r['HCDF']*100:.1f}%  (earning fuel remaining)")
        print(f"  Career type: {self.career_type}  λ={r['career_lambda']}")
        print(f"  Years to retirement: {r['T_years']}")
        lder_str = f"{r['LDER']:.3f}" if r['LDER'] is not None else "∞"
        print(f"  LDER = {lder_str}  →  {r['lder_label']}")
        if r['teh_warning']:
            print(f"\n  {r['teh_warning']}")
        print()


# ── Layer 3: Debt Quality ─────────────────────────────────────────────────────

@dataclass
class ModelV2Layer3:
    """Debt quality assessment — fed by portfolio's VM categories."""
    portfolio_inputs: dict   # from DebtPortfolio.to_model_v2_inputs()

    @property
    def dominant_category(self) -> str:
        return self.portfolio_inputs.get("dominant_vm_category", "neutral")

    @property
    def drag_ratio(self) -> float:
        return self.portfolio_inputs.get("drag_ratio", 0.0)

    @property
    def debt_type_badge(self) -> str:
        c = self.dominant_category
        if c == "slingshot":
            return "🚀 Gravitational Slingshot"
        elif c == "neutral":
            return "🛸 Neutral"
        else:
            return "🪨 Pure Drag"

    def compute(self) -> dict:
        return {
            "dominant_vm_category": self.dominant_category,
            "debt_type_badge":      self.debt_type_badge,
            "drag_ratio_pct":       round(self.drag_ratio * 100, 1),
            "slingshot_balance":    self.portfolio_inputs.get("slingshot_balance", 0),
            "drag_balance":         self.portfolio_inputs.get("drag_balance", 0),
            "debt_breakdown":       self.portfolio_inputs.get("debt_breakdown", []),
        }

    def print_layer3(self):
        r = self.compute()
        print(f"  ── LAYER 3: DEBT QUALITY ─────────────────────────────")
        print(f"  Badge:        {r['debt_type_badge']}")
        print(f"  Drag ratio:   {r['drag_ratio_pct']:.1f}%  of total debt is pure drag")
        print(f"  Slingshot ฿:  ฿{r['slingshot_balance']:>10,.0f}")
        print(f"  Drag ฿:       ฿{r['drag_balance']:>10,.0f}")
        print(f"{'='*60}\n")
