"""
payoff_strategy.py
──────────────────
Payoff Strategy Engine — Model V.2 extension.

Simulates month-by-month debt paydown across 5 strategies and ranks them
by Binding Energy (total interest paid = true cost of remaining in debt).

Strategies:
  Avalanche      — highest rate first  (mathematically optimal)
  Snowball       — smallest balance first  (psychologically optimal)
  Hybrid         — weighted blend (default α=0.6 toward avalanche)
  Retention      — negotiate rate reduction with existing creditor (no fees)
  Refinancing    — replace high-rate debt with a lower-rate loan
  Consolidation  — merge multiple debts into a single loan

Architecture:
  SimDebt            lightweight mutable debt for simulation
  MonthlySnapshot    per-month state snapshot
  StrategyResult     output dataclass per strategy
  _simulate()        core month-by-month simulation engine
  apply_*()          portfolio transformation helpers
  StrategyEngine     runs all combinations, ranks by binding energy

Usage:
    from payoff_strategy import StrategyEngine
    engine = StrategyEngine(portfolio, extra_monthly=1_000)
    engine.print_report()
"""

from __future__ import annotations
import math
import copy
from dataclasses import dataclass, field
from typing import Optional, Callable, List

from debt_portfolio  import DebtPortfolio
from debt_instruments import DebtInstrument


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Simulation primitives
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimDebt:
    """
    Lightweight, mutable debt record used only inside the simulator.
    Copied from DebtInstrument at strategy start — never mutates the original.
    """
    name:         str
    balance:      float
    monthly_rate: float      # compound-equivalent monthly rate (decimal)
    min_payment:  float      # contractual minimum monthly payment
    vm_category:  str  = "neutral"
    paid_off:     bool = False

    @property
    def monthly_interest(self) -> float:
        return self.balance * self.monthly_rate

    @property
    def effective_annual_rate(self) -> float:
        """Compound-equivalent annual rate."""
        return (1 + self.monthly_rate) ** 12 - 1

    @classmethod
    def from_instrument(cls, d: DebtInstrument) -> "SimDebt":
        return cls(
            name         = d.name,
            balance      = d.current_balance,
            monthly_rate = d._effective_monthly_rate,
            min_payment  = d.monthly_payment,
            vm_category  = d.purpose.vm_category,
        )


@dataclass
class MonthlySnapshot:
    """State of the portfolio at end of a given month."""
    month:           int
    total_balance:   float
    total_interest:  float      # interest charged this month
    total_payment:   float      # total cash paid this month
    weighted_rate:   float      # portfolio weighted annual rate
    debts_remaining: int        # number of debts still active


@dataclass
class StrategyResult:
    """Output produced by running one strategy through the simulator."""
    strategy_name:           str
    binding_energy:          float    # total interest paid over life of debt (฿)
    months_to_freedom:       int      # months until last debt = 0
    be_savings:              float    # interest saved vs minimum-payment baseline
    be_savings_pct:          float    # savings as % of baseline binding energy
    required_extra_monthly:  float    # extra ฿/month above current minimums
    monthly_snapshots:       list     # list[MonthlySnapshot]
    description:             str = ""

    @property
    def years_to_freedom(self) -> float:
        return round(self.months_to_freedom / 12, 1)

    @property
    def years_str(self) -> str:
        y = int(self.months_to_freedom // 12)
        m = int(self.months_to_freedom % 12)
        if y == 0:
            return f"{m}mo"
        if m == 0:
            return f"{y}y"
        return f"{y}y {m}mo"


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Core simulation engine
# ─────────────────────────────────────────────────────────────────────────────

def _simulate(
    sim_debts:     list,          # list[SimDebt]
    extra_monthly: float,
    priority_fn:   Callable,
    max_months:    int = 600,
) -> tuple:
    """
    Month-by-month debt paydown simulation.
    Refactored with precise interest accrual and rolling cascade logic.
    """
    debts = [copy.copy(d) for d in sim_debts]
    for d in debts:
        d.paid_off = False
        d.balance  = max(d.balance, 0.0)

    total_interest = 0.0
    extra_pool     = extra_monthly   # เติบโตขึ้นเมื่อมีหนี้ถูกเคลียร์หมด (Cascade)
    snapshots      = []
    month          = 0

    while month < max_months:
        month += 1
        
        # คัดกรองหนี้ที่ยังจ่ายไม่หมด ณ ต้นเดือนจริง ๆ
        active = [d for d in debts if not d.paid_off and d.balance > 0.01]
        if not active:
            break

        month_interest = 0.0
        month_payment  = 0.0

        # Step 1 — ทบดอกเบี้ยเข้าเงินต้นก่อน แล้วตัดจ่ายด้วยเงินขั้นต่ำ (ตามที่คุณดีไซน์)
        for d in active:
            interest = d.balance * d.monthly_rate
            d.balance += interest
            
            # ยอดที่ต้องจ่ายจริงในงวดนี้ (ไม่เกินยอดหนี้รวมดอกเบี้ย)
            actual_min_payment = min(d.min_payment, d.balance)
            d.balance -= actual_min_payment
            
            # บันทึกสถิติเม็ดเงิน
            total_interest += interest
            month_interest += interest
            month_payment  += actual_min_payment

        # Step 2 — นำเงิน Extra Pool ไปโปะหนี้ตามลำดับความสำคัญ (กรองเฉพาะก้อนที่ยังเหลือเงินต้น > 0.01)
        priority_order = priority_fn([d for d in debts if not d.paid_off and d.balance > 0.01])
        remaining_extra = extra_pool
        
        for pd in priority_order:
            if remaining_extra <= 0.01:
                break
            applied          = min(remaining_extra, pd.balance)
            pd.balance      -= applied
            remaining_extra -= applied
            month_payment   += applied

        # Step 3 — ตรวจสอบหนี้ที่เคลียร์จบในเดือนนี้ มาร์กปิดบัญชี และส่งต่อเงินขั้นต่ำเข้าคาสเคด
        for d in active:
            if d.balance <= 0.01 and not d.paid_off:
                d.paid_off  = True
                d.balance   = 0.0
                extra_pool += d.min_payment  # คาสเคดเงินขั้นต่ำเพื่อไปใช้ทบโปะก้อนอื่นในเดือนถัดไป

        # บันทึกสถานะสิ้นเดือน (Snapshot)
        alive     = [d for d in debts if not d.paid_off]
        total_bal = sum(d.balance for d in alive)
        w_rate    = (
            sum(d.balance * d.effective_annual_rate for d in alive) / total_bal
            if total_bal > 0 else 0.0
        )
        
        snapshots.append(MonthlySnapshot(
            month           = month,
            total_balance   = round(total_bal, 2),
            total_interest  = round(month_interest, 2),
            total_payment   = round(month_payment, 2),
            weighted_rate   = round(w_rate, 4),
            debts_remaining = len(alive),
        ))

    return round(total_interest, 2), month, snapshots



# ─────────────────────────────────────────────────────────────────────────────
# 3.  Priority functions
# ─────────────────────────────────────────────────────────────────────────────

def _avalanche_priority(debts: list) -> list:
    """Highest effective annual rate first — minimises total interest."""
    return sorted(debts, key=lambda d: d.monthly_rate, reverse=True)


def _snowball_priority(debts: list) -> list:
    """Smallest balance first — maximises psychological quick wins."""
    return sorted(debts, key=lambda d: d.balance)


def _hybrid_priority(alpha: float) -> Callable:
    """
    Weighted blend.
    alpha = 1.0 → pure avalanche  (rate dominates)
    alpha = 0.0 → pure snowball   (balance dominates)
    alpha = 0.6 → near-optimal and psychologically sustainable (default)
    """
    def fn(debts: list) -> list:
        if not debts:
            return debts
        max_rate    = max(d.monthly_rate for d in debts) or 1e-9
        max_balance = max(d.balance      for d in debts) or 1e-9
        def score(d):
            r_norm = d.monthly_rate / max_rate
            b_norm = 1.0 - d.balance / max_balance
            return alpha * r_norm + (1.0 - alpha) * b_norm
        return sorted(debts, key=score, reverse=True)
    return fn


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Portfolio transformation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _amortizing_payment(balance: float, monthly_rate: float, n_months: int) -> float:
    """Standard annuity payment formula."""
    if n_months <= 0 or balance <= 0:
        return balance
    if monthly_rate == 0:
        return balance / n_months
    return balance * monthly_rate / (1 - (1 + monthly_rate) ** (-n_months))


def apply_retention(
    sim_debts:       list,
    target_name:     str,
    new_annual_rate: float,    # negotiated annual rate (decimal)
    new_term_months: int = 60, # re-amortise over this term
) -> list:
    """
    Retention: reduce the interest rate on a single debt via negotiation.
    No fees. Recomputes the minimum payment at the new rate.
    """
    debts = [copy.copy(d) for d in sim_debts]
    for d in debts:
        if d.name == target_name:
            new_r         = new_annual_rate / 12
            d.min_payment = max(
                _amortizing_payment(d.balance, new_r, new_term_months),
                d.monthly_interest,   # floor: at least cover interest
            )
            d.monthly_rate = new_r
    return debts


def apply_retention_all(
    sim_debts:       list,
    rate_reduction:  float,    # reduction in annual rate (decimal), e.g. 0.04
    new_term_months: int = 60,
) -> list:
    """Apply a flat rate reduction to every debt simultaneously."""
    debts = [copy.copy(d) for d in sim_debts]
    for d in debts:
        new_annual = max(0.01, d.effective_annual_rate - rate_reduction)
        new_r      = new_annual / 12
        d.min_payment  = max(
            _amortizing_payment(d.balance, new_r, new_term_months),
            d.balance * new_r,
        )
        d.monthly_rate = new_r
    return debts


def apply_refinance(
    sim_debts:       list,
    target_name:     str,
    new_annual_rate: float,
    new_term_months: int,
    fee_pct:         float = 0.01,   # % of balance as upfront fee
    fee_fixed:       float = 0.0,
) -> list:
    """
    Refinancing: replace a debt with a new loan at a lower rate.
    The refinancing fee is added to the new loan balance.
    """
    debts = [copy.copy(d) for d in sim_debts]
    for d in debts:
        if d.name == target_name:
            fee            = d.balance * fee_pct + fee_fixed
            new_balance    = d.balance + fee
            new_r          = new_annual_rate / 12
            d.balance      = new_balance
            d.monthly_rate = new_r
            d.min_payment  = _amortizing_payment(new_balance, new_r, new_term_months)
    return debts


def apply_consolidation(
    sim_debts:       list,
    names_to_merge:  Optional[list] = None,   # None = merge all debts
    new_annual_rate: float = 0.12,
    new_term_months: int   = 60,
    fee_pct:         float = 0.015,
    fee_fixed:       float = 0.0,
) -> list:
    """
    Consolidation: merge selected debts into a single new loan.
    Remaining debts (not merged) are kept unchanged.
    The consolidation fee is rolled into the new loan balance.
    """
    if names_to_merge is None:
        names_to_merge = [d.name for d in sim_debts]

    to_merge = [d for d in sim_debts if d.name in names_to_merge]
    keep     = [copy.copy(d) for d in sim_debts if d.name not in names_to_merge]

    if not to_merge:
        return [copy.copy(d) for d in sim_debts]

    total_bal   = sum(d.balance for d in to_merge)
    fee         = total_bal * fee_pct + fee_fixed
    new_balance = total_bal + fee
    new_r       = new_annual_rate / 12
    new_pmt     = _amortizing_payment(new_balance, new_r, new_term_months)

    consolidated = SimDebt(
        name         = f"Consolidated [{new_annual_rate*100:.1f}% / {new_term_months}mo]",
        balance      = new_balance,
        monthly_rate = new_r,
        min_payment  = new_pmt,
        vm_category  = "neutral",
    )
    return keep + [consolidated]


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Strategy Engine
# ─────────────────────────────────────────────────────────────────────────────

class StrategyEngine:
    """
    Runs all debt-reduction strategy combinations on a DebtPortfolio
    and ranks them by Binding Energy (total interest paid).

    Parameters
    ----------
    portfolio           : DebtPortfolio  — source of debt instruments
    extra_monthly       : float          — extra ฿/month the person can commit
    refi_rate           : float          — target rate for refinanced debt
    refi_term_months    : int            — term for refinanced loan
    refi_fee_pct        : float          — refinancing fee as % of balance
    consolidation_rate  : float          — rate for consolidated loan
    consol_term_months  : int            — term for consolidated loan
    consol_fee_pct      : float          — consolidation fee as % of balance
    retention_reduction : float          — annual rate reduction via negotiation
    retention_term      : int            — re-amortisation term after retention

    Usage
    -----
        engine = StrategyEngine(portfolio, extra_monthly=1_000)
        results = engine.run_all()
        engine.print_report()
    """

    def __init__(
        self,
        portfolio:           DebtPortfolio,
        extra_monthly:       float = 0.0,
        refi_rate:           float = 0.12,
        refi_term_months:    int   = 60,
        refi_fee_pct:        float = 0.01,
        consolidation_rate:  float = 0.12,
        consol_term_months:  int   = 60,
        consol_fee_pct:      float = 0.015,
        retention_reduction: float = 0.04,
        retention_term:      int   = 60,
    ):
        self.portfolio           = portfolio
        self.extra_monthly       = extra_monthly
        self.refi_rate           = refi_rate
        self.refi_term_months    = refi_term_months
        self.refi_fee_pct        = refi_fee_pct
        self.consolidation_rate  = consolidation_rate
        self.consol_term_months  = consol_term_months
        self.consol_fee_pct      = consol_fee_pct
        self.retention_reduction = retention_reduction
        self.retention_term      = retention_term

        self._base: list = [SimDebt.from_instrument(d) for d in portfolio.debts]
        self._baseline: Optional[StrategyResult] = None

    # ── Internal helper ──────────────────────────────────────────────────────

    def _run(
        self,
        name:        str,
        debts:       list,
        priority_fn: Callable,
        extra:       Optional[float] = None,
        description: str = "",
    ) -> StrategyResult:
        extra = self.extra_monthly if extra is None else extra
        be, months, snaps = _simulate(debts, extra, priority_fn)

        savings     = (self._baseline.binding_energy - be) if self._baseline else 0.0
        savings_pct = (savings / self._baseline.binding_energy * 100
                       if self._baseline and self._baseline.binding_energy > 0 else 0.0)

        return StrategyResult(
            strategy_name          = name,
            binding_energy         = be,
            months_to_freedom      = months,
            be_savings             = round(savings, 2),
            be_savings_pct         = round(savings_pct, 1),
            required_extra_monthly = extra,
            monthly_snapshots      = snaps,
            description            = description,
        )

    # ── Public API ───────────────────────────────────────────────────────────

    def run_all(self) -> list:
        """Run every strategy + combination. Returns list[StrategyResult] sorted by binding energy."""
        results = []

        # ── 0. Baseline — minimum payments, no extra, still cascades freed ──
        self._baseline = self._run(
            name        = "Minimum Payments (Baseline)",
            debts       = self._base,
            priority_fn = _avalanche_priority,
            extra       = 0.0,
            description = "Pay minimums only. Freed payments cascade to next debt. "
                          "Shows true cost of no extra effort.",
        )
        self._baseline.be_savings     = 0.0
        self._baseline.be_savings_pct = 0.0
        results.append(self._baseline)

        # ── 1. Pure Avalanche ────────────────────────────────────────────────
        results.append(self._run(
            name        = "Avalanche",
            debts       = self._base,
            priority_fn = _avalanche_priority,
            description = "Highest rate first. Mathematically optimal — minimum total interest.",
        ))

        # ── 2. Pure Snowball ─────────────────────────────────────────────────
        results.append(self._run(
            name        = "Snowball",
            debts       = self._base,
            priority_fn = _snowball_priority,
            description = "Smallest balance first. Quick wins build momentum.",
        ))

        # ── 3. Hybrid α=0.6 ─────────────────────────────────────────────────
        results.append(self._run(
            name        = "Hybrid (α=0.6)",
            debts       = self._base,
            priority_fn = _hybrid_priority(0.6),
            description = "60% rate priority + 40% balance priority. Near-optimal and sustainable.",
        ))

        # ── 4. Retention — per debt ──────────────────────────────────────────
        for d in self._base:
            new_rate = max(0.01, d.effective_annual_rate - self.retention_reduction)
            if new_rate < d.effective_annual_rate - 0.001:
                retained = apply_retention(self._base, d.name, new_rate, self.retention_term)
                results.append(self._run(
                    name        = f"Retention: {d.name[:28]} → {new_rate*100:.1f}%",
                    debts       = retained,
                    priority_fn = _hybrid_priority(0.6),
                    description = f"Negotiate -{self.retention_reduction*100:.0f}% on '{d.name}'. "
                                  f"No fees. Then Hybrid payoff.",
                ))

        # ── 5. Retention — all debts ─────────────────────────────────────────
        all_retained = apply_retention_all(
            self._base, self.retention_reduction, self.retention_term
        )
        results.append(self._run(
            name        = "Retention: All Debts + Avalanche",
            debts       = all_retained,
            priority_fn = _avalanche_priority,
            description = f"Negotiate -{self.retention_reduction*100:.0f}% rate reduction with "
                          f"every creditor, then avalanche.",
        ))
        results.append(self._run(
            name        = "Retention: All Debts + Hybrid",
            debts       = all_retained,
            priority_fn = _hybrid_priority(0.6),
            description = f"Negotiate -{self.retention_reduction*100:.0f}% on all debts, then Hybrid.",
        ))

        # ── 6. Refinance — highest-rate debt ─────────────────────────────────
        if self._base:
            hr = max(self._base, key=lambda d: d.monthly_rate)
            if hr.effective_annual_rate > self.refi_rate + 0.01:
                refi_debts = apply_refinance(
                    self._base, hr.name,
                    self.refi_rate, self.refi_term_months,
                    fee_pct=self.refi_fee_pct,
                )
                results.append(self._run(
                    name        = f"Refinance: {hr.name[:22]} → {self.refi_rate*100:.1f}%",
                    debts       = refi_debts,
                    priority_fn = _hybrid_priority(0.6),
                    description = f"Replace highest-rate debt with {self.refi_rate*100:.1f}% loan "
                                  f"({self.refi_term_months}mo). Fee={self.refi_fee_pct*100:.1f}% added to balance.",
                ))
                # Refinance + Avalanche
                results.append(self._run(
                    name        = f"Refinance → Avalanche",
                    debts       = refi_debts,
                    priority_fn = _avalanche_priority,
                    description = f"Refinance highest-rate debt to {self.refi_rate*100:.1f}%, "
                                  f"then avalanche remaining.",
                ))

        # ── 7. Consolidation — all debts ─────────────────────────────────────
        if len(self._base) > 1:
            all_consol = apply_consolidation(
                self._base,
                names_to_merge  = None,
                new_annual_rate = self.consolidation_rate,
                new_term_months = self.consol_term_months,
                fee_pct         = self.consol_fee_pct,
            )
            results.append(self._run(
                name        = f"Consolidate All → {self.consolidation_rate*100:.1f}%",
                debts       = all_consol,
                priority_fn = _hybrid_priority(0.6),
                description = f"Merge all debts → single {self.consolidation_rate*100:.1f}% loan "
                              f"/ {self.consol_term_months}mo. Fee={self.consol_fee_pct*100:.1f}%.",
            ))
            results.append(self._run(
                name        = f"Consolidate All → Avalanche",
                debts       = all_consol,
                priority_fn = _avalanche_priority,
                description = "Consolidate all, then avalanche extra payments.",
            ))

        # ── 8. Consolidation — drag debts only ───────────────────────────────
        drag_names = [d.name for d in self._base if d.vm_category == "drag"]
        if len(drag_names) > 1:
            drag_consol = apply_consolidation(
                self._base,
                names_to_merge  = drag_names,
                new_annual_rate = self.consolidation_rate,
                new_term_months = self.consol_term_months,
                fee_pct         = self.consol_fee_pct,
            )
            results.append(self._run(
                name        = f"Consolidate Drag Debts → {self.consolidation_rate*100:.1f}%",
                debts       = drag_consol,
                priority_fn = _hybrid_priority(0.6),
                description = "Consolidate only high-cost drag debts (CC/personal). "
                              "Retain slingshot debts (mortgage/education) separately.",
            ))

        # ── 9. Retention + Consolidation combo ───────────────────────────────
        retained_then_consol = apply_consolidation(
            all_retained,
            names_to_merge  = None,
            new_annual_rate = self.consolidation_rate,
            new_term_months = self.consol_term_months,
            fee_pct         = self.consol_fee_pct,
        )
        results.append(self._run(
            name        = "Retention + Consolidate → Avalanche",
            debts       = retained_then_consol,
            priority_fn = _avalanche_priority,
            description = "Negotiate rate reduction on all debts first, then consolidate "
                          "into one loan and avalanche.",
        ))

        # ── Sort: baseline first, rest by binding_energy ascending ───────────
        baseline = results[0]
        rest     = sorted(results[1:], key=lambda r: r.binding_energy)
        return [baseline] + rest

    # ── Display ──────────────────────────────────────────────────────────────

    def print_report(self, top_n: int = 8):
        """Run all strategies and print a ranked comparison table."""
        results = self.run_all()
        baseline = results[0]

        print(f"\n{'='*74}")
        print(f"  PAYOFF STRATEGY ENGINE")
        print(f"  {self.portfolio.label}  |  "
              f"D=฿{self.portfolio.D:,.0f}  "
              f"r={self.portfolio.r*100:.1f}%  "
              f"P=฿{self.portfolio.P:,.0f}/mo")
        print(f"  Extra available: ฿{self.extra_monthly:,.0f}/mo")
        print(f"{'='*74}")

        # ── Strategy comparison table ────────────────────────────────────
        print(f"\n  STRATEGY COMPARISON")
        print(f"  {'Strategy':<42} {'Int. Paid':>10} {'Freedom':>8} {'Saved':>10} {'Save%':>6}")
        print(f"  {'-'*42} {'-'*10} {'-'*8} {'-'*10} {'-'*6}")

        for i, r in enumerate(results[:top_n + 1]):
            if i == 0:
                tag = "  📍"
            elif i == 1:
                tag = "  🥇"
            elif i == 2:
                tag = "  🥈"
            elif i == 3:
                tag = "  🥉"
            else:
                tag = f"   {i} "

            saved_str = f"฿{r.be_savings:>7,.0f}"  if r.be_savings > 0 else "  baseline"
            pct_str   = f"{r.be_savings_pct:.1f}%" if r.be_savings > 0 else "—"
            print(f"{tag} {r.strategy_name:<40} ฿{r.binding_energy:>8,.0f} "
                  f"{r.years_str:>8} {saved_str:>10} {pct_str:>6}")

        # ── Top recommendation ───────────────────────────────────────────
        best = results[1]
        print(f"\n  {'─'*72}")
        print(f"  📌  BEST STRATEGY: {best.strategy_name}")
        print(f"      {best.description}")
        print(f"      Total interest  : ฿{best.binding_energy:,.0f}  (vs ฿{baseline.binding_energy:,.0f} baseline)")
        print(f"      Interest saved  : ฿{best.be_savings:,.0f}  ({best.be_savings_pct:.1f}%)")
        print(f"      Debt-free in    : {best.years_str}  "
              f"(vs {baseline.years_str} baseline)")
        print(f"      Extra required  : ฿{best.required_extra_monthly:,.0f}/mo above current payments")
        print(f"{'='*74}\n")
