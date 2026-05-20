"""
debt_portfolio.py
─────────────────
Portfolio aggregator — combines all individual DebtInstrument objects
into the exact variables that Model V.2 Layer 1 / Layer 2 needs.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional
from debt_instruments import DebtInstrument, DebtPurpose


@dataclass
class DebtPortfolio:
    debts: list[DebtInstrument] = field(default_factory=list)
    label: str = "My Debt Portfolio"

    def add(self, debt: DebtInstrument) -> "DebtPortfolio":
        self.debts.append(debt)
        return self

    # ── Core aggregates ────────────────────────────────────────────────────────

    @property
    def D(self) -> float:
        return sum(d.current_balance for d in self.debts)

    @property
    def P(self) -> float:
        return sum(d.monthly_payment for d in self.debts)

    @property
    def r(self) -> float:
        total_D = self.D
        if total_D <= 0:
            return 0.0
        return sum(d.effective_annual_rate * d.current_balance for d in self.debts) / total_D

    @property
    def total_monthly_interest(self) -> float:
        return sum(d.monthly_interest_charge for d in self.debts)

    @property
    def net_principal_paid_monthly(self) -> float:
        return self.P - self.total_monthly_interest

    @property
    def total_remaining_months(self) -> float:
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
        return self.P <= self.total_monthly_interest

    # ── κ portfolio ───────────────────────────────────────────────────────────

    @property
    def kappa_portfolio(self) -> float:
        """Balance-weighted escape velocity multiple across all debts.

        κ_portfolio = Σ (D_i / D_total) × (P*_i / F_g_i)

        Each debt contributes its own κ_i weighted by its share of
        total outstanding balance. More accurate than using aggregate
        D/r/P when debts have very different rates or terms.
        Falls back to 1.0 when a debt has no interest charge.
        """
        total_D = self.D
        if total_D <= 0:
            return 1.0

        weighted_kappa = 0.0
        for d in self.debts:
            fg_i = d.monthly_interest_charge
            if fg_i <= 0:
                # Zero-interest debt: κ_i = 1 (neutral)
                kappa_i = 1.0
            else:
                ps_i    = d.escape_velocity_payment
                kappa_i = ps_i / fg_i
            weight = d.current_balance / total_D
            weighted_kappa += weight * kappa_i

        return weighted_kappa

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
        return sorted(self.debts, key=lambda d: d.effective_annual_rate, reverse=True)

    def snowball_order(self) -> list[DebtInstrument]:
        return sorted(self.debts, key=lambda d: d.current_balance)

    # ── Layer 3: Debt Quality ─────────────────────────────────────────────────

    @property
    def drag_debt_balance(self) -> float:
        return sum(d.current_balance for d in self.debts if d.purpose.vm_category == "drag")

    @property
    def slingshot_debt_balance(self) -> float:
        return sum(d.current_balance for d in self.debts if d.purpose.vm_category == "slingshot")

    @property
    def drag_ratio(self) -> float:
        return self.drag_debt_balance / self.D if self.D > 0 else 0.0

    @property
    def dominant_vm_category(self) -> str:
        cats = {"slingshot": self.slingshot_debt_balance,
                "drag":      self.drag_debt_balance,
                "neutral":   self.D - self.slingshot_debt_balance - self.drag_debt_balance}
        return max(cats, key=cats.get)

    # ── Model V.2 interface ───────────────────────────────────────────────────

    def to_model_v2_inputs(self) -> dict:
        return {
            # Layer 1 core
            "D": round(self.D, 2),
            "r": round(self.r, 6),
            "P": round(self.P, 2),

            # κ for β formula
            "kappa_portfolio": round(self.kappa_portfolio, 4),

            # Diagnostics
            "monthly_interest_total":    round(self.total_monthly_interest, 2),
            "monthly_principal_total":   round(self.net_principal_paid_monthly, 2),
            "portfolio_months_remaining":round(self.total_remaining_months, 1)
                                          if self.total_remaining_months != math.inf else None,
            "is_orbit_locked":           self.is_orbit_locked,
            "num_debts":                 len(self.debts),

            # Layer 3
            "dominant_vm_category": self.dominant_vm_category,
            "drag_ratio":           round(self.drag_ratio, 4),
            "slingshot_balance":    round(self.slingshot_debt_balance, 2),
            "drag_balance":         round(self.drag_debt_balance, 2),

            # Per-debt breakdown
            "debt_breakdown":  [d.summary() for d in self.debts],
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
        print(f"  κ portfolio:              {self.kappa_portfolio:>10.4f}")
        t = self.total_remaining_months
        t_str = f"{t:.1f} months" if t != math.inf else "∞ (ORBIT LOCKED)"
        print(f"  Time to Debt Freedom:     {t_str}")
        print(f"  Orbit Locked:             {self.is_orbit_locked}")
        print(f"  Drag Debt Ratio:          {self.drag_ratio*100:.1f}%")
        print(f"\n  {'Debt':<25} {'Balance':>12} {'Rate':>8} {'Payment':>10} {'Months':>8}")
        print(f"  {'-'*25} {'-'*12} {'-'*8} {'-'*10} {'-'*8}")
        for d in self.debts:
            t_i   = d.months_remaining
            t_str = f"{t_i:.0f}" if t_i != math.inf else "∞"
            locked = " 🔴" if d.is_orbit_locked else ""
            print(f"  {d.name:<25} ฿{d.current_balance:>11,.0f} {d.effective_annual_rate*100:>7.2f}% "
                  f"฿{d.monthly_payment:>9,.0f} {t_str:>8}{locked}")
        print(f"\n  Model V.2 Inputs → D=฿{self.D:,.0f}  r={self.r*100:.2f}%  P=฿{self.P:,.0f}  κ={self.kappa_portfolio:.4f}")
        print(f"{'='*60}\n")