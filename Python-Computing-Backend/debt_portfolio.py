"""
debt_portfolio.py
─────────────────
Portfolio aggregator — combines all individual DebtInstrument objects
into the exact variables that Model V.2 Layer 1 / Layer 2 / Layer 3 needs.

Model V.2 requires:
  D   → total outstanding debt balance
  r   → weighted-average annual interest rate (compound-equivalent)
  P   → total monthly debt payment
  
  Plus for Layer 3:
  debt_purpose distribution → VM category
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional
from debt_instruments import DebtInstrument, DebtPurpose


@dataclass
class DebtPortfolio:
    """
    Aggregates a list of DebtInstrument objects into Model V.2 core inputs.

    Usage:
        portfolio = DebtPortfolio(debts=[debt1, debt2, debt3])
        model_inputs = portfolio.to_model_v2_inputs()
    """
    debts: list[DebtInstrument] = field(default_factory=list)
    label: str = "My Debt Portfolio"

    def add(self, debt: DebtInstrument) -> "DebtPortfolio":
        """Fluent method: portfolio.add(debt1).add(debt2)"""
        self.debts.append(debt)
        return self

    # ── Core aggregates ────────────────────────────────────────────────────────

    @property
    def D(self) -> float:
        """Total outstanding debt balance — Model V.2 D."""
        return sum(d.current_balance for d in self.debts)

    @property
    def P(self) -> float:
        """Total monthly debt payments — Model V.2 P."""
        return sum(d.monthly_payment for d in self.debts)

    @property
    def r(self) -> float:
        """
        Balance-weighted average effective annual rate — Model V.2 r.
        
        Formula: r = Σ(r_i * D_i) / Σ(D_i)
        
        This gives Model V.2's Debt Gravity F_g = D * (r/12) the right value
        because D * r_weighted = Σ D_i * r_i = Σ monthly_interest_i.
        """
        total_D = self.D
        if total_D <= 0:
            return 0.0
        return sum(d.effective_annual_rate * d.current_balance for d in self.debts) / total_D

    @property
    def total_monthly_interest(self) -> float:
        """Σ F_g_i — total interest cost this month across all debts."""
        return sum(d.monthly_interest_charge for d in self.debts)

    @property
    def net_principal_paid_monthly(self) -> float:
        """P - Σ(monthly interest) — how much actual debt is shrinking per month."""
        return self.P - self.total_monthly_interest

    @property
    def total_remaining_months(self) -> float:
        """
        Portfolio-level t* using Model V.2 formula with aggregated D, r, P.
        Note: this is DIFFERENT from max(t_i) — it's the aggregate payoff time.
        """
        r_monthly = self.r / 12
        D = self.D
        P = self.P
        if D <= 0:
            return 0.0
        if r_monthly == 0:
            return D / P if P > 0 else math.inf
        if P <= D * r_monthly:
            return math.inf
        return -math.log(1 - (D * r_monthly / P)) / r_monthly

    @property
    def is_orbit_locked(self) -> bool:
        """True if total payments don't cover total monthly interest."""
        return self.P <= self.total_monthly_interest

    # ── Orbit lock analysis per debt ──────────────────────────────────────────

    def orbit_locked_debts(self) -> list[DebtInstrument]:
        return [d for d in self.debts if d.is_orbit_locked]

    def highest_rate_debt(self) -> Optional[DebtInstrument]:
        if not self.debts:
            return None
        return max(self.debts, key=lambda d: d.effective_annual_rate)

    def highest_balance_debt(self) -> Optional[DebtInstrument]:
        if not self.debts:
            return None
        return max(self.debts, key=lambda d: d.current_balance)

    # ── Payoff strategy helpers ───────────────────────────────────────────────

    def avalanche_order(self) -> list[DebtInstrument]:
        """Highest interest rate first — mathematically optimal."""
        return sorted(self.debts, key=lambda d: d.effective_annual_rate, reverse=True)

    def snowball_order(self) -> list[DebtInstrument]:
        """Smallest balance first — psychologically effective."""
        return sorted(self.debts, key=lambda d: d.current_balance)

    # ── Layer 3: Debt Quality ─────────────────────────────────────────────────

    @property
    def drag_debt_balance(self) -> float:
        """Total balance in 'drag' category (credit card, personal consumption)."""
        return sum(d.current_balance for d in self.debts if d.purpose.vm_category == "drag")

    @property
    def slingshot_debt_balance(self) -> float:
        """Total balance in 'slingshot' category (mortgage, education, business)."""
        return sum(d.current_balance for d in self.debts if d.purpose.vm_category == "slingshot")

    @property
    def drag_ratio(self) -> float:
        """Fraction of total debt that is pure drag (0 = all good, 1 = all bad)."""
        return self.drag_debt_balance / self.D if self.D > 0 else 0.0

    @property
    def dominant_vm_category(self) -> str:
        """Returns 'slingshot', 'neutral', or 'drag' based on largest balance share."""
        cats = {"slingshot": self.slingshot_debt_balance,
                "drag":      self.drag_debt_balance,
                "neutral":   self.D - self.slingshot_debt_balance - self.drag_debt_balance}
        return max(cats, key=cats.get)

    # ── Model V.2 interface ───────────────────────────────────────────────────

    def to_model_v2_inputs(self) -> dict:
        """
        Returns exactly what Model V.2 needs as a clean dict.
        Feed this directly into your Model_V2 class.
        """
        return {
            # Layer 1 core
            "D":  round(self.D, 2),
            "r":  round(self.r, 6),              # compound-equivalent annual rate
            "P":  round(self.P, 2),

            # Diagnostics (not in model formula, but useful for UI)
            "monthly_interest_total":    round(self.total_monthly_interest, 2),
            "monthly_principal_total":   round(self.net_principal_paid_monthly, 2),
            "portfolio_months_remaining":round(self.total_remaining_months, 1)
                                          if self.total_remaining_months != math.inf else None,
            "is_orbit_locked":           self.is_orbit_locked,
            "num_debts":                 len(self.debts),

            # Layer 3
            "dominant_vm_category":      self.dominant_vm_category,
            "drag_ratio":                round(self.drag_ratio, 4),
            "slingshot_balance":         round(self.slingshot_debt_balance, 2),
            "drag_balance":              round(self.drag_debt_balance, 2),

            # Per-debt breakdown (for UI display and audit)
            "debt_breakdown": [d.summary() for d in self.debts],

            # Payoff recommendations
            "avalanche_order": [d.name for d in self.avalanche_order()],
            "snowball_order":  [d.name for d in self.snowball_order()],
        }

    # ── Display ───────────────────────────────────────────────────────────────

    def print_summary(self):
        print(f"\n{'='*60}")
        print(f"  DEBT PORTFOLIO: {self.label}")
        print(f"{'='*60}")
        print(f"  Total Debt (D):          ฿{self.D:>12,.2f}")
        print(f"  Weighted Rate (r):        {self.r*100:>10.2f}%  p.a.")
        print(f"  Total Payment (P):       ฿{self.P:>12,.2f} / month")
        print(f"  Monthly Interest:        ฿{self.total_monthly_interest:>12,.2f} / month")
        print(f"  Monthly Principal:       ฿{self.net_principal_paid_monthly:>12,.2f} / month")
        t = self.total_remaining_months
        t_str = f"{t:.1f} months" if t != math.inf else "∞ (ORBIT LOCKED)"
        print(f"  Time to Debt Freedom:     {t_str}")
        print(f"  Orbit Locked:             {self.is_orbit_locked}")
        print(f"  Drag Debt Ratio:          {self.drag_ratio*100:.1f}%")
        print(f"\n  {'Debt':<25} {'Balance':>12} {'Rate':>8} {'Payment':>10} {'Months':>8}")
        print(f"  {'-'*25} {'-'*12} {'-'*8} {'-'*10} {'-'*8}")
        for d in self.debts:
            t_i = d.months_remaining
            t_str = f"{t_i:.0f}" if t_i != math.inf else "∞"
            locked = " 🔴" if d.is_orbit_locked else ""
            print(f"  {d.name:<25} ฿{d.current_balance:>11,.0f} {d.effective_annual_rate*100:>7.2f}% ฿{d.monthly_payment:>9,.0f} {t_str:>8}{locked}")
        print(f"\n  Model V.2 Inputs → D=฿{self.D:,.0f}  r={self.r*100:.2f}%  P=฿{self.P:,.0f}")
        print(f"{'='*60}\n")
