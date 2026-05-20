"""
debt_instruments_extended.py
────────────────────────────
Extension module for debt_instruments.py.
Adds 5 new instrument types + PayoffCalculator.

New classes:
  StepUpRateDebt      – scheduled rate changes at fixed future dates (mortgage promo periods)
  FloatingRateDebt    – MRR/MLR-linked with spread; rate updated externally
  AnnualStepUpDebt    – rate increases by fixed increment each year (กยศ. student loan pattern)
  CooperativeDebt     – savings-linked, dividend offsets net interest cost
  PayoffCalculator    – get_payoff_amount(as_of_date) across ALL debt types, with full fee stack

Import alongside debt_instruments.py:
  from debt_instruments import *
  from debt_instruments_extended import *
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from debt_instruments import DebtInstrument, DebtPurpose, InterestType


# ─────────────────────────────────────────────────────────────────────────────
# 1. STEP-UP RATE DEBT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RatePeriod:
    """
    A single rate segment in a step-up schedule.
      months_duration : how many months this rate applies
      annual_rate     : decimal (e.g. 0.035 for 3.5%)
      label           : human-readable tag e.g. "Year 1-3 promo"
    """
    months_duration: int
    annual_rate:     float
    label:           str = ""


@dataclass
class StepUpRateDebt(DebtInstrument):
    """
    Mortgage or loan with multiple scheduled rate periods.
    Classic Thai bank pattern:
      Year 1   : MRR - 3.0%  (promo)
      Year 2-3 : MRR - 2.0%  (transition)
      Year 4+  : MRR - 0.5%  (standard)

    rate_schedule : list[RatePeriod] — ordered, covers the full term.
    months_elapsed: how many months of the schedule have ALREADY been served.
                    Used to start the simulation mid-schedule.

    The "current" rate (for portfolio snapshot) is whichever period we are in now.
    annual_rate on the base class is set to the CURRENT period rate for compatibility.
    """
    rate_schedule:   list = field(default_factory=list)   # list[RatePeriod]
    months_elapsed:  int  = 0    # months already paid before simulation starts

    def __post_init__(self):
        if not self.rate_schedule:
            raise ValueError("StepUpRateDebt requires at least one RatePeriod in rate_schedule.")
        # Set base class annual_rate to CURRENT period rate (for summary/portfolio snapshot)
        self.annual_rate   = self._rate_at_month(self.months_elapsed)
        self.interest_type = InterestType.EFFECTIVE

        # remaining_term_months: use original_term_months minus elapsed as source of truth.
        # Cap any RatePeriod of 9999 at actual loan term so the formula stays finite.
        if self.remaining_term_months is None:
            if self.original_term_months:
                self.remaining_term_months = max(0, self.original_term_months - self.months_elapsed)
            else:
                # Sum finite periods; treat last period as open-ended via original_term_months
                finite_sum = sum(
                    p.months_duration for p in self.rate_schedule if p.months_duration < 9000
                )
                self.remaining_term_months = max(finite_sum - self.months_elapsed, 1)
        super().__post_init__()

    def _rate_at_month(self, absolute_month: int) -> float:
        """
        Given an absolute month index (0-based from loan start),
        return the annual rate for that month.
        Falls back to final period's rate after schedule ends.
        """
        cursor = 0
        for period in self.rate_schedule:
            cursor += period.months_duration
            if absolute_month < cursor:
                return period.annual_rate
        return self.rate_schedule[-1].annual_rate   # past all defined periods

    def _period_label_at_month(self, absolute_month: int) -> str:
        cursor = 0
        for period in self.rate_schedule:
            cursor += period.months_duration
            if absolute_month < cursor:
                return period.label or f"Rate {period.annual_rate*100:.2f}%"
        return self.rate_schedule[-1].label or "Final rate"

    def _compute_effective_monthly_rate(self) -> float:
        """Current monthly rate — used only for portfolio snapshot & base class compat."""
        return self._rate_at_month(self.months_elapsed) / 12

    def _compute_monthly_payment(self) -> float:
        """
        Compute payment using current balance and remaining term at current rate.
        Will be re-computed each month in the schedule anyway.
        """
        r = self._effective_monthly_rate
        n = self.remaining_term_months or 1
        D = self.current_balance
        if r == 0:
            return max(D / n, self.minimum_payment)
        return max(D * r / (1 - (1 + r) ** (-n)), self.minimum_payment)

    def amortization_schedule(self) -> list[dict]:
        """
        Step-through month by month, recomputing payment each time the rate changes.
        Each row annotates which rate period is active.
        """
        schedule = []
        balance  = self.current_balance
        cum_int  = 0.0
        sim_month = 0                          # relative month in simulation
        abs_month = self.months_elapsed        # absolute month from loan start

        total_remaining = self.remaining_term_months or (
            sum(p.months_duration for p in self.rate_schedule) - self.months_elapsed
        )

        while balance > 0.01 and sim_month < total_remaining and sim_month < 1200:
            r_annual = self._rate_at_month(abs_month)
            r_monthly = r_annual / 12
            months_left = total_remaining - sim_month

            # Recompute payment for this period (re-amortize on remaining balance)
            if r_monthly == 0:
                pmt = balance / months_left
            else:
                pmt = balance * r_monthly / (1 - (1 + r_monthly) ** (-months_left))
            pmt = max(pmt, self.minimum_payment)

            interest  = balance * r_monthly
            principal = min(pmt - interest, balance)
            if principal < 0:
                principal = 0
            balance  -= principal
            cum_int  += interest
            sim_month += 1
            abs_month += 1

            schedule.append({
                "month":               sim_month,
                "rate_period_label":   self._period_label_at_month(abs_month - 1),
                "annual_rate_pct":     round(r_annual * 100, 4),
                "balance_start":       round(balance + principal, 2),
                "interest_charge":     round(interest, 2),
                "principal_paid":      round(principal, 2),
                "payment":             round(pmt, 2),
                "balance_end":         round(max(balance, 0), 2),
                "cumulative_interest": round(cum_int, 2),
                "orbit_locked":        pmt <= interest,
            })

            if balance <= 0:
                break

        return schedule

    @property
    def current_rate_label(self) -> str:
        return self._period_label_at_month(self.months_elapsed)

    @property
    def months_in_current_period_remaining(self) -> int:
        """How many months until the next rate change."""
        cursor = 0
        for period in self.rate_schedule:
            cursor += period.months_duration
            if self.months_elapsed < cursor:
                return cursor - self.months_elapsed
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. FLOATING RATE DEBT  (MRR / MLR linked)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FloatingRateDebt(DebtInstrument):
    """
    Rate = reference_rate + spread  (can be negative spread = discount)

    Thai bank examples:
      - MRR (Minimum Retail Rate)   ~6.5-7.5% currently
      - MLR (Minimum Lending Rate)  ~5.5-6.5% currently
      - THBFIX / THOR               money market reference

    reference_rate : current reference rate as decimal (e.g. 0.0725 for 7.25%)
    spread         : additive spread, can be negative (e.g. -0.015 = MRR-1.5%)
    reference_name : label for UI ("MRR", "MLR", "THOR", etc.)

    For Model V.2: use current effective rate as the snapshot.
    For scenario analysis: call .set_reference_rate(new_rate) and re-run.

    stress_scenarios : list of (delta_rate, label) to test rate shock sensitivity
    """
    reference_rate:  float = 0.0725   # current reference rate (decimal)
    spread:          float = -0.015   # spread (decimal); negative = discount
    reference_name:  str   = "MRR"

    def __post_init__(self):
        self.interest_type = InterestType.EFFECTIVE
        # annual_rate = reference + spread (the actual rate charged)
        self.annual_rate   = self.current_effective_rate
        super().__post_init__()

    @property
    def current_effective_rate(self) -> float:
        """r_eff = reference + spread. Floor at 0."""
        return max(0.0, self.reference_rate + self.spread)

    def _compute_effective_monthly_rate(self) -> float:
        return self.current_effective_rate / 12

    def set_reference_rate(self, new_reference_rate: float):
        """
        Update the reference rate (e.g. after a BOT policy change).
        Recalculates all derived values.
        """
        self.reference_rate = new_reference_rate
        self.annual_rate    = self.current_effective_rate
        self._effective_monthly_rate = self._compute_effective_monthly_rate()
        self._computed_payment       = self._compute_monthly_payment()

    def rate_shock_analysis(self, shocks: list[float]) -> list[dict]:
        """
        shocks: list of rate delta decimals, e.g. [0.005, 0.01, 0.02]
        Returns impact on monthly payment and months_remaining for each shock.
        """
        results = []
        original_ref = self.reference_rate
        for delta in shocks:
            self.set_reference_rate(original_ref + delta)
            results.append({
                "shock_bps":           round(delta * 10000),
                "new_reference_pct":   round(self.reference_rate * 100, 4),
                "new_effective_pct":   round(self.current_effective_rate * 100, 4),
                "new_monthly_payment": round(self.monthly_payment, 2),
                "new_monthly_interest":round(self.monthly_interest_charge, 2),
                "months_remaining":    round(self.months_remaining, 1)
                                       if self.months_remaining != math.inf else None,
                "orbit_locked":        self.is_orbit_locked,
            })
        self.set_reference_rate(original_ref)   # restore
        return results

    def summary(self) -> dict:
        s = super().summary()
        s.update({
            "reference_rate_name": self.reference_name,
            "reference_rate_pct":  round(self.reference_rate * 100, 4),
            "spread_pct":          round(self.spread * 100, 4),
            "effective_rate_pct":  round(self.current_effective_rate * 100, 4),
        })
        return s


# ─────────────────────────────────────────────────────────────────────────────
# 3. ANNUAL STEP-UP DEBT  (กยศ. student loan pattern)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AnnualStepUpDebt(DebtInstrument):
    """
    Rate starts at initial_rate and increases by step_rate each year,
    capped at max_rate. Payment recomputed at each annual reset.

    กยศ. (กองทุนเงินให้กู้ยืมเพื่อการศึกษา) pattern:
      - Year 1 repayment: 1%
      - Year 2:           2%
      - ...
      - Year 7+:          capped at actual contract rate (typ. 1% simple for กยศ.)

    Also covers bank products that step up by 0.5% per year for 3 years then fix.

    years_elapsed   : years already repaid (determines current rate tier)
    initial_rate    : rate in year 1 of REPAYMENT phase
    step_rate       : annual increment (decimal)
    max_rate        : ceiling rate
    grace_months    : months of grace period (interest may still accrue)
    """
    initial_rate:  float = 0.01     # 1% for กยศ. year 1
    step_rate:     float = 0.01     # +1% per year
    max_rate:      float = 0.01     # กยศ. caps at 1% (simple) — override for others
    years_elapsed: int   = 0        # years already in repayment
    grace_months:  int   = 0        # grace period (interest accrues, no payment)
    simple_interest: bool = True    # กยศ. uses simple interest, not compound

    def __post_init__(self):
        self.interest_type = InterestType.EFFECTIVE
        self.annual_rate   = self._rate_for_year(self.years_elapsed)
        super().__post_init__()

    def _rate_for_year(self, year: int) -> float:
        """Return annual rate for given repayment year (0-indexed)."""
        return min(self.initial_rate + year * self.step_rate, self.max_rate)

    def _compute_effective_monthly_rate(self) -> float:
        if self.simple_interest:
            # Simple interest: monthly = annual / 12  (no compounding)
            return self._rate_for_year(self.years_elapsed) / 12
        return self._rate_for_year(self.years_elapsed) / 12

    def amortization_schedule(self) -> list[dict]:
        """
        Month-by-month, resetting rate every 12 months.
        Handles grace period (balance grows, no payment required).
        """
        schedule    = []
        balance     = self.current_balance
        cum_int     = 0.0
        sim_month   = 0
        abs_year    = self.years_elapsed
        total_months = self.remaining_term_months or (self.original_term_months or 120)

        # Grace period
        for g in range(self.grace_months):
            r_m      = self._rate_for_year(abs_year) / 12
            interest = balance * r_m if not self.simple_interest else balance * self._rate_for_year(abs_year) / 12
            balance += interest      # interest capitalizes during grace
            cum_int += interest
            sim_month += 1
            schedule.append({
                "month":               sim_month,
                "phase":               "grace",
                "annual_rate_pct":     round(self._rate_for_year(abs_year) * 100, 4),
                "balance_start":       round(balance - interest, 2),
                "interest_charge":     round(interest, 2),
                "principal_paid":      0.0,
                "payment":             0.0,
                "balance_end":         round(balance, 2),
                "cumulative_interest": round(cum_int, 2),
                "orbit_locked":        False,
            })

        # Repayment phase
        month_in_year = 0
        repay_months  = total_months - self.grace_months

        while balance > 0.01 and sim_month < total_months + self.grace_months and sim_month < 1200:
            # Reset rate at year boundary
            if month_in_year == 12:
                month_in_year = 0
                abs_year     += 1

            r_annual  = self._rate_for_year(abs_year)
            r_monthly = r_annual / 12

            months_left = repay_months - (sim_month - self.grace_months)
            if months_left <= 0:
                break

            # Recompute payment for remaining balance at current rate
            if r_monthly == 0 or self.simple_interest:
                # Simple interest: fixed principal + flat monthly interest
                principal_part = balance / months_left
                interest       = balance * r_monthly
                pmt = principal_part + interest
            else:
                interest = balance * r_monthly
                pmt = balance * r_monthly / (1 - (1 + r_monthly) ** (-months_left))

            pmt       = max(pmt, self.minimum_payment)
            principal = min(pmt - interest, balance)
            if principal < 0:
                principal = 0
            balance  -= principal
            cum_int  += interest
            sim_month    += 1
            month_in_year += 1

            schedule.append({
                "month":               sim_month,
                "phase":               "repayment",
                "repayment_year":      abs_year + 1,
                "annual_rate_pct":     round(r_annual * 100, 4),
                "balance_start":       round(balance + principal, 2),
                "interest_charge":     round(interest, 2),
                "principal_paid":      round(principal, 2),
                "payment":             round(pmt, 2),
                "balance_end":         round(max(balance, 0), 2),
                "cumulative_interest": round(cum_int, 2),
                "orbit_locked":        pmt <= interest,
            })

            if balance <= 0:
                break

        return schedule

    @property
    def current_annual_rate(self) -> float:
        return self._rate_for_year(self.years_elapsed)

    @property
    def next_year_rate(self) -> float:
        return self._rate_for_year(self.years_elapsed + 1)

    def rate_ladder(self) -> list[dict]:
        """Show the full rate schedule over all years."""
        years = []
        for y in range(20):
            r = self._rate_for_year(y)
            years.append({"repayment_year": y + 1, "annual_rate_pct": round(r * 100, 4)})
            if r >= self.max_rate and y > 0:
                break
        return years


# ─────────────────────────────────────────────────────────────────────────────
# 4. COOPERATIVE DEBT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CooperativeDebt(DebtInstrument):
    """
    Savings cooperative loan (สหกรณ์ออมทรัพย์).
    
    Key mechanics:
    1. Member must hold share_subscription (หุ้น) — a savings deposit
    2. Cooperative pays dividend_yield on shares annually
    3. Net interest cost = loan_rate - effective dividend offset
    4. Share subscription is often automatically deducted from salary
    5. Some cooperatives allow share balance to offset principal at any time

    Fields:
      share_subscription    : current share balance held (฿)  ← earns dividend
      monthly_share_deposit : monthly contribution to shares (฿)
      dividend_yield        : annual dividend rate on shares (decimal)
      loan_rate             : gross annual loan interest rate (decimal)
      share_offset_allowed  : if True, shares can be used to reduce principal

    Net effective rate for Model V.2:
      r_net = loan_rate - (share_subscription / current_balance) * dividend_yield
      (capped at 0 — cooperative can't pay you to hold the loan)
    """
    share_subscription:    float = 0.0    # current shares held (฿)
    monthly_share_deposit: float = 0.0    # ongoing monthly share contribution
    dividend_yield:        float = 0.065  # typical Thai cooperative: 5-7%
    loan_rate:             float = 0.06   # gross loan interest rate
    share_offset_allowed:  bool  = True   # can shares be applied to principal?
    # Payoff: some coops allow netting shares against outstanding balance
    shares_netted_at_payoff: bool = True  # net share balance from payoff amount?

    def __post_init__(self):
        self.interest_type = InterestType.EFFECTIVE
        self.annual_rate   = self.loan_rate   # gross; net computed separately
        super().__post_init__()

    @property
    def gross_monthly_interest(self) -> float:
        """Full interest charge before dividend offset."""
        return self.current_balance * (self.loan_rate / 12)

    @property
    def monthly_dividend_offset(self) -> float:
        """
        Monthly equivalent of dividend earned on share subscription.
        Dividend is paid annually, but we smooth to monthly for cash flow purposes.
        """
        return self.share_subscription * (self.dividend_yield / 12)

    @property
    def net_monthly_interest(self) -> float:
        """Actual cost of holding the debt after dividend offset."""
        return max(0.0, self.gross_monthly_interest - self.monthly_dividend_offset)

    @property
    def net_effective_annual_rate(self) -> float:
        """
        Effective annual rate after dividend benefit.
        This is what feeds Model V.2's r_i for portfolio weighting.
        """
        net_monthly = self.net_monthly_interest / self.current_balance if self.current_balance > 0 else 0
        return (1 + net_monthly) ** 12 - 1

    @property
    def effective_annual_rate(self) -> float:
        """Override: use net rate for Model V.2 portfolio weighting."""
        return self.net_effective_annual_rate

    @property
    def monthly_interest_charge(self) -> float:
        """Override: use net cost for F_g calculation."""
        return self.net_monthly_interest

    def _compute_effective_monthly_rate(self) -> float:
        """Net monthly rate for amortization."""
        if self.current_balance <= 0:
            return self.loan_rate / 12
        net_monthly = self.net_monthly_interest / self.current_balance
        return max(net_monthly, 0.0)

    @property
    def net_monthly_outflow(self) -> float:
        """
        Total cash actually leaving the member's account per month.
        = monthly_payment + monthly_share_deposit
        (This is the TRUE cash flow burden, even though share deposit builds equity)
        """
        return self.monthly_payment + self.monthly_share_deposit

    @property
    def payoff_balance_after_share_netting(self) -> float:
        """
        If shares can be netted: payoff amount = max(0, balance - shares)
        This is the actual cash needed to fully exit the cooperative debt.
        """
        if self.shares_netted_at_payoff:
            return max(0.0, self.current_balance - self.share_subscription)
        return self.current_balance

    def amortization_schedule(self) -> list[dict]:
        """
        Includes growing share subscription and annual dividend events.
        """
        schedule     = []
        balance      = self.current_balance
        shares       = self.share_subscription
        cum_int      = 0.0
        cum_dividend = 0.0
        month        = 0

        while balance > 0.01 and month < 1200:
            month        += 1
            gross_int     = balance * (self.loan_rate / 12)
            div_offset    = shares  * (self.dividend_yield / 12)
            net_int       = max(0.0, gross_int - div_offset)
            pmt           = self.monthly_payment
            principal     = min(pmt - net_int, balance)
            if principal < 0:
                principal = 0
            balance      -= principal
            shares       += self.monthly_share_deposit
            cum_int      += net_int
            cum_dividend += div_offset

            is_dividend_month = (month % 12 == 0)
            schedule.append({
                "month":                 month,
                "balance_start":         round(balance + principal, 2),
                "gross_interest":        round(gross_int, 2),
                "dividend_offset":       round(div_offset, 2),
                "net_interest":          round(net_int, 2),
                "principal_paid":        round(principal, 2),
                "payment":               round(pmt, 2),
                "share_deposit":         round(self.monthly_share_deposit, 2),
                "share_balance":         round(shares, 2),
                "balance_end":           round(max(balance, 0), 2),
                "cumulative_net_interest":round(cum_int, 2),
                "cumulative_dividend":   round(cum_dividend, 2),
                "is_annual_dividend":    is_dividend_month,
                "orbit_locked":          pmt <= net_int,
            })

            if balance <= 0:
                break

        return schedule

    def dividend_benefit_summary(self) -> dict:
        """Total interest savings from share dividend over full schedule."""
        sched = self.amortization_schedule()
        total_gross  = sum(r["gross_interest"] for r in sched)
        total_div    = sum(r["dividend_offset"] for r in sched)
        total_net    = sum(r["net_interest"] for r in sched)
        return {
            "total_gross_interest":   round(total_gross, 2),
            "total_dividend_savings": round(total_div, 2),
            "total_net_interest":     round(total_net, 2),
            "savings_pct":            round(total_div / total_gross * 100, 1) if total_gross > 0 else 0,
        }

    def summary(self) -> dict:
        s = super().summary()
        s.update({
            "loan_rate_pct":            round(self.loan_rate * 100, 4),
            "dividend_yield_pct":       round(self.dividend_yield * 100, 4),
            "net_effective_rate_pct":   round(self.net_effective_annual_rate * 100, 4),
            "share_subscription":       round(self.share_subscription, 2),
            "monthly_dividend_offset":  round(self.monthly_dividend_offset, 2),
            "net_monthly_outflow":      round(self.net_monthly_outflow, 2),
            "payoff_after_netting":     round(self.payoff_balance_after_share_netting, 2),
        })
        return s


# ─────────────────────────────────────────────────────────────────────────────
# 5. PAYOFF CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PayoffFeeConfig:
    """
    Full fee configuration for payoff amount calculation.
    All fees are ADDITIVE on top of the outstanding principal + accrued interest.

    early_close_fee_pct   : % of REMAINING BALANCE charged for prepayment
                            (e.g. 0.02 = 2% — common Thai mortgage prepayment penalty)
    early_close_fee_fixed : flat fixed penalty (฿)
    early_close_fee_waived_after_year: year after which penalty is waived (0 = never waived)

    mortgage_release_fee  : Land Department fee to discharge mortgage from title deed
                            typically ฿ fixed or % of appraised value
    mortgage_release_pct  : alternatively as % of remaining balance

    stamp_duty_pct        : stamp duty on mortgage discharge document
                            standard = 0.0005 (0.05%) of the mortgage amount registered

    legal_fee_fixed       : lawyer / bank officer fee
    valuation_fee         : property re-valuation (sometimes required)
    insurance_refund      : credit life / mortgage fire insurance refund (negative fee)
    other_fees_fixed      : any other flat fees
    """
    # Prepayment penalty
    early_close_fee_pct:           float = 0.0
    early_close_fee_fixed:         float = 0.0
    early_close_fee_waived_after_year: int = 0   # 0 = never waived

    # Mortgage-specific release fees
    mortgage_release_fee_fixed:    float = 0.0
    mortgage_release_fee_pct:      float = 0.0   # % of balance
    stamp_duty_pct:                float = 0.0005  # 0.05% standard

    # Other
    legal_fee_fixed:               float = 0.0
    valuation_fee:                 float = 0.0
    insurance_refund:              float = 0.0   # negative = money back to borrower
    other_fees_fixed:              float = 0.0

    # Cooperative-specific
    share_netting:                 bool  = False  # apply share balance against principal


# Pre-built fee configs for common Thai products
MORTGAGE_FEE_CONFIG = PayoffFeeConfig(
    early_close_fee_pct            = 0.02,    # 2% within first 3 years
    early_close_fee_waived_after_year = 3,
    mortgage_release_fee_fixed     = 100.0,   # ฿100 Land Dept base
    stamp_duty_pct                 = 0.0005,  # 0.05% of registered mortgage
    legal_fee_fixed                = 2_000.0, # typical bank processing
)

PERSONAL_LOAN_FEE_CONFIG = PayoffFeeConfig(
    early_close_fee_pct  = 0.01,   # 1% common for personal loans
    legal_fee_fixed      = 500.0,
)

COOPERATIVE_FEE_CONFIG = PayoffFeeConfig(
    share_netting = True,   # most coops net shares
)

NO_FEE_CONFIG = PayoffFeeConfig()


class PayoffCalculator:
    """
    Compute the true cash amount needed to fully close any DebtInstrument
    as of a given date, including all applicable fees.

    Works with ALL instrument types. For CooperativeDebt it handles
    share netting automatically.

    Usage:
        calc = PayoffCalculator(debt, fee_config=MORTGAGE_FEE_CONFIG, sign_date=date(2022,3,1))
        result = calc.get_payoff_amount(as_of_date=date.today())
        print(result)
    """

    def __init__(self,
                 debt:        DebtInstrument,
                 fee_config:  PayoffFeeConfig = None,
                 sign_date:   Optional[date]  = None):
        self.debt       = debt
        self.fee_config = fee_config or NO_FEE_CONFIG
        self.sign_date  = sign_date or debt.sign_date

    def _years_since_signing(self, as_of_date: date) -> float:
        if self.sign_date is None:
            return 999.0  # assume fully past any lock-in
        delta = as_of_date - self.sign_date
        return delta.days / 365.25

    def _accrued_interest_to_date(self, as_of_date: date) -> float:
        """
        Accrued interest from last payment date to as_of_date (daily accrual).
        Uses current effective monthly rate / 30 as daily rate.
        """
        last_pmt = self.debt.payment_date
        if last_pmt is None:
            return 0.0
        days = max(0, (as_of_date - last_pmt).days)
        daily_rate = self.debt._effective_monthly_rate / 30
        return self.debt.current_balance * daily_rate * days

    def get_payoff_amount(self, as_of_date: Optional[date] = None) -> dict:
        """
        Returns a full breakdown of the payoff amount as of as_of_date.

        Returns dict with:
          outstanding_principal : current balance
          accrued_interest      : interest accrued since last payment
          subtotal              : principal + accrued interest
          early_close_penalty   : prepayment fee (0 if waived)
          mortgage_release_fee  : land dept discharge fee
          stamp_duty            : 0.05% stamp duty on mortgage amount
          legal_fee             : processing/legal
          valuation_fee         : re-valuation
          insurance_refund      : refund (negative)
          other_fees            : other
          share_netting         : share balance deducted (negative, CoopDebt only)
          total_payoff          : CASH NEEDED TO CLOSE TODAY
          fee_breakdown         : list of (label, amount) for UI display
          is_penalty_waived     : True if past lock-in period
        """
        if as_of_date is None:
            as_of_date = date.today()

        fc                = self.fee_config
        D                 = self.debt.current_balance
        accrued           = self._accrued_interest_to_date(as_of_date)
        subtotal          = D + accrued
        years_held        = self._years_since_signing(as_of_date)

        # ── Early close penalty ───────────────────────────────────────────────
        penalty_waived = (
            fc.early_close_fee_waived_after_year > 0
            and years_held >= fc.early_close_fee_waived_after_year
        )
        if penalty_waived:
            early_close = 0.0
        else:
            early_close = (D * fc.early_close_fee_pct) + fc.early_close_fee_fixed

        # ── Mortgage release fee ──────────────────────────────────────────────
        mortgage_release = (
            fc.mortgage_release_fee_fixed
            + D * fc.mortgage_release_fee_pct
        )

        # ── Stamp duty ────────────────────────────────────────────────────────
        # Stamp duty is on the REGISTERED mortgage amount (use original_balance as proxy)
        registered_amount = self.debt.original_balance or D
        stamp_duty = registered_amount * fc.stamp_duty_pct

        # ── Other fees ────────────────────────────────────────────────────────
        legal_fee      = fc.legal_fee_fixed
        valuation_fee  = fc.valuation_fee
        insurance_ref  = -abs(fc.insurance_refund)  # always negative (refund)
        other_fees     = fc.other_fees_fixed

        # ── Share netting (CooperativeDebt) ──────────────────────────────────
        share_netting_amount = 0.0
        if fc.share_netting and isinstance(self.debt, CooperativeDebt):
            share_netting_amount = -self.debt.share_subscription  # negative = deduction

        # ── Total ─────────────────────────────────────────────────────────────
        total_payoff = (
            subtotal
            + early_close
            + mortgage_release
            + stamp_duty
            + legal_fee
            + valuation_fee
            + insurance_ref
            + other_fees
            + share_netting_amount
        )

        # ── Fee breakdown for UI ──────────────────────────────────────────────
        fee_breakdown = []
        fee_breakdown.append(("Outstanding principal",  round(D, 2)))
        if accrued > 0.01:
            fee_breakdown.append(("Accrued interest (daily)", round(accrued, 2)))
        if early_close > 0:
            fee_breakdown.append((f"Prepayment penalty ({fc.early_close_fee_pct*100:.1f}%)", round(early_close, 2)))
        elif penalty_waived and (fc.early_close_fee_pct > 0 or fc.early_close_fee_fixed > 0):
            fee_breakdown.append(("Prepayment penalty", 0.0, "WAIVED (past lock-in)"))
        if mortgage_release > 0:
            fee_breakdown.append(("Mortgage release fee", round(mortgage_release, 2)))
        if stamp_duty > 0:
            fee_breakdown.append(("Stamp duty (0.05%)", round(stamp_duty, 2)))
        if legal_fee > 0:
            fee_breakdown.append(("Legal / processing fee", round(legal_fee, 2)))
        if valuation_fee > 0:
            fee_breakdown.append(("Valuation fee", round(valuation_fee, 2)))
        if insurance_ref < 0:
            fee_breakdown.append(("Insurance refund", round(insurance_ref, 2)))
        if other_fees > 0:
            fee_breakdown.append(("Other fees", round(other_fees, 2)))
        if share_netting_amount < 0:
            fee_breakdown.append(("Share subscription (netted)", round(share_netting_amount, 2)))

        return {
            "debt_name":            self.debt.name,
            "as_of_date":           as_of_date.isoformat(),
            "outstanding_principal":round(D, 2),
            "accrued_interest":     round(accrued, 2),
            "subtotal":             round(subtotal, 2),
            "early_close_penalty":  round(early_close, 2),
            "is_penalty_waived":    penalty_waived,
            "mortgage_release_fee": round(mortgage_release, 2),
            "stamp_duty":           round(stamp_duty, 2),
            "legal_fee":            round(legal_fee, 2),
            "valuation_fee":        round(valuation_fee, 2),
            "insurance_refund":     round(insurance_ref, 2),
            "other_fees":           round(other_fees, 2),
            "share_netting":        round(share_netting_amount, 2),
            "total_payoff":         round(max(total_payoff, 0), 2),
            "fee_breakdown":        fee_breakdown,
            "years_held":           round(years_held, 2),
        }

    def print_payoff(self, as_of_date: Optional[date] = None):
        r = self.get_payoff_amount(as_of_date)
        print(f"\n  ── PAYOFF QUOTE: {r['debt_name']} ──")
        print(f"  As of: {r['as_of_date']}  ({r['years_held']:.1f} years held)")
        print(f"  {'─'*45}")
        for item in r['fee_breakdown']:
            label  = item[0]
            amount = item[1]
            note   = item[2] if len(item) > 2 else ""
            sign   = "+" if amount >= 0 else ""
            note_str = f"  [{note}]" if note else ""
            print(f"  {label:<35} {sign}฿{amount:>10,.2f}{note_str}")
        print(f"  {'─'*45}")
        print(f"  {'TOTAL CASH TO CLOSE':<35}  ฿{r['total_payoff']:>10,.2f}")
        if r['is_penalty_waived']:
            print(f"  ✅ Prepayment penalty waived (held > {self.fee_config.early_close_fee_waived_after_year} years)")
        print()