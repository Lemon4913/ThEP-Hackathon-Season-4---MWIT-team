"""
debt_instruments.py
───────────────────
Individual debt computation engine for Model V.2 pipeline.

Architecture:
  DebtInstrument (base)
    ├── FlatRateDebt          – personal loans, some car loans (Thai ธนาคาร-style)
    ├── EffectiveRateDebt     – mortgages, credit lines (standard amortization)
    ├── CompoundRateDebt      – savings-linked / compound-accrual products
    ├── CreditCardDebt        – revolving, minimum payment logic
    ├── HirePurchaseDebt      – flat-rate installment, common in Thailand
    └── BalloonDebt           – period loan with lump-sum at end

Each instrument exposes:
  .current_balance           → D_i  (outstanding principal right now)
  .effective_annual_rate     → r_i  (annualized, always compound-equivalent)
  .monthly_payment           → P_i
  .monthly_interest_charge   → F_g_i  (actual ฿ interest this month)
  .months_remaining          → t_i
  .is_orbit_locked           → True if payment < monthly interest
  .amortization_schedule()   → list of monthly snapshots
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


# ── Enums ─────────────────────────────────────────────────────────────────────

class InterestType(Enum):
    FLAT = "flat_rate"
    EFFECTIVE = "effective_rate"      # = compound, APR-style
    COMPOUND = "compound_rate"        # explicit compound (same math, clearer intent)


class DebtPurpose(Enum):
    """For Model V.2 Layer 3 debt quality classification."""
    EDUCATION       = "education"
    MORTGAGE        = "mortgage"
    BUSINESS        = "business"
    AUTO            = "auto"
    MIXED           = "mixed"
    CREDIT_CARD     = "credit_card"
    PERSONAL        = "personal_consumption"
    MEDICAL         = "medical"
    OTHER           = "other"

    @property
    def vm_category(self) -> str:
        """Returns 'slingshot', 'neutral', or 'drag' for Layer 3."""
        slingshot = {self.EDUCATION, self.MORTGAGE, self.BUSINESS}
        drag      = {self.CREDIT_CARD, self.PERSONAL}
        if self in slingshot:
            return "slingshot"
        elif self in drag:
            return "drag"
        return "neutral"


# ── Base dataclass ─────────────────────────────────────────────────────────────

@dataclass
class DebtInstrument:
    """
    Base class. Subclasses must implement:
      - _compute_effective_monthly_rate() -> float
      - _compute_monthly_payment()        -> float   (if not user-supplied)
      - amortization_schedule()           -> list[dict]
    """
    # ── Identity
    name:                str  = "Unnamed Debt"
    creditor:            str  = "Unknown"
    purpose:     DebtPurpose  = DebtPurpose.OTHER

    # ── Core financials (user inputs)
    original_balance:    float = 0.0   # total debt at signing
    current_balance:     float = 0.0   # outstanding RIGHT NOW  ← D_i
    annual_rate:         float = 0.0   # as decimal e.g. 0.18 for 18%
    interest_type: InterestType = InterestType.EFFECTIVE
    minimum_payment:     float = 0.0   # user's minimum monthly obligation

    # ── Optional fields
    original_term_months: Optional[int]   = None  # total contract term
    remaining_term_months: Optional[int]  = None  # months left (if known)
    sign_date:         Optional[date]     = None
    payment_cutoff_day: Optional[int]     = None  # day of month payment due
    payment_date:       Optional[date]    = None  # next scheduled payment
    early_close_fee:     float            = 0.0   # penalty for early payoff
    other_fees:          float            = 0.0   # annual fee, etc.

    # ── Computed (populated by __post_init__)
    _effective_monthly_rate: float = field(default=0.0, init=False, repr=False)
    _computed_payment:       float = field(default=0.0, init=False, repr=False)

    def __post_init__(self):
        if self.current_balance <= 0:
            self.current_balance = self.original_balance
        self._effective_monthly_rate = self._compute_effective_monthly_rate()
        self._computed_payment       = self._compute_monthly_payment()

    # ── Override in subclasses ─────────────────────────────────────────────────

    def _compute_effective_monthly_rate(self) -> float:
        """Return monthly rate as decimal, compound-equivalent."""
        if self.interest_type == InterestType.FLAT:
            # Flat rate → approximate effective annual rate via IRR shortcut
            # EAR ≈ flat_rate * 1.8  (standard Thai/ASEAN approximation)
            # More precise: solve for r_eff from annuity equation
            return self._flat_to_effective_monthly(self.annual_rate,
                                                   self.original_term_months or 12)
        else:
            # EFFECTIVE / COMPOUND — treat as nominal annual / 12
            return self.annual_rate / 12

    def _compute_monthly_payment(self) -> float:
        """Standard amortizing payment formula. Override for special products."""
        r = self._effective_monthly_rate
        n = self.remaining_term_months or self.original_term_months
        D = self.current_balance
        if not n or n <= 0:
            return self.minimum_payment
        if r == 0:
            return D / n
        payment = D * r / (1 - (1 + r) ** (-n))
        # Always respect minimum payment
        return max(payment, self.minimum_payment)

    def amortization_schedule(self) -> list[dict]:
        """
        Returns month-by-month snapshot.
        Each dict: {month, balance_start, interest, principal, balance_end, cumulative_interest}
        """
        schedule = []
        balance = self.current_balance
        r       = self._effective_monthly_rate
        pmt     = self.monthly_payment
        cum_int = 0.0
        month   = 0

        while balance > 0.01 and month < 1200:  # 100-year safety cap
            month     += 1
            interest   = balance * r
            principal  = min(pmt - interest, balance)
            if principal <= 0:
                # Orbit lock — interest exceeds payment
                principal = 0
                balance  += interest - pmt
            else:
                balance   -= principal
            cum_int   += interest

            schedule.append({
                "month":              month,
                "balance_start":      balance + principal,
                "interest_charge":    round(interest, 2),
                "principal_paid":     round(principal, 2),
                "payment":            round(pmt, 2),
                "balance_end":        round(max(balance, 0), 2),
                "cumulative_interest":round(cum_int, 2),
                "orbit_locked":       principal <= 0,
            })

            if balance <= 0:
                break

        return schedule

    # ── Properties (Model V.2 interface) ──────────────────────────────────────

    @property
    def monthly_payment(self) -> float:
        """P_i — actual payment being made (max of computed and minimum)."""
        return self._computed_payment

    @property
    def monthly_interest_charge(self) -> float:
        """F_g_i = D_i * r_i/12 — the gravity of this debt this month."""
        return self.current_balance * self._effective_monthly_rate

    @property
    def effective_annual_rate(self) -> float:
        """r_i — compound-equivalent annual rate (for portfolio weighting)."""
        return (1 + self._effective_monthly_rate) ** 12 - 1

    @property
    def months_remaining(self) -> float:
        """t* for this debt using Model V.2 amortization formula."""
        r = self._effective_monthly_rate
        P = self.monthly_payment
        D = self.current_balance
        if D <= 0:
            return 0.0
        if r == 0:
            return D / P if P > 0 else math.inf
        monthly_interest = D * r
        if P <= monthly_interest:
            return math.inf  # orbit lock
        return -math.log(1 - (D * r / P)) / r

    @property
    def is_orbit_locked(self) -> bool:
        return self.monthly_payment <= self.monthly_interest_charge

    @property
    def escape_velocity_payment(self, target_months: int = 60) -> float:
        """P* — minimum payment to clear this debt in target_months."""
        r = self._effective_monthly_rate
        D = self.current_balance
        n = target_months
        if r == 0:
            return D / n
        return D * r / (1 - (1 + r) ** (-n))

    def summary(self) -> dict:
        """Full summary dict for portfolio aggregation and display."""
        return {
            "name":                  self.name,
            "creditor":              self.creditor,
            "purpose":               self.purpose.value,
            "vm_category":           self.purpose.vm_category,
            "current_balance":       round(self.current_balance, 2),
            "annual_rate_input":     round(self.annual_rate * 100, 4),
            "interest_type":         self.interest_type.value,
            "effective_annual_rate": round(self.effective_annual_rate * 100, 4),
            "effective_monthly_rate":round(self._effective_monthly_rate * 100, 6),
            "monthly_payment":       round(self.monthly_payment, 2),
            "monthly_interest_charge":round(self.monthly_interest_charge, 2),
            "months_remaining":      round(self.months_remaining, 1) if self.months_remaining != math.inf else "∞",
            "is_orbit_locked":       self.is_orbit_locked,
            "early_close_fee":       self.early_close_fee,
            "other_fees":            self.other_fees,
        }

    # ── Static helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _flat_to_effective_monthly(flat_annual_rate: float,
                                   term_months: int) -> float:
        """
        Convert flat rate to compound-equivalent monthly rate via Newton's method.
        Solves: D = sum_{t=1}^{n} PMT / (1+r)^t  where PMT = D*(1 + flat*T/12)/n
        """
        if flat_annual_rate == 0:
            return 0.0
        n   = term_months
        T   = n / 12
        # Total repayment per unit of debt
        total_per_unit = 1 + flat_annual_rate * T
        pmt_per_unit   = total_per_unit / n

        # Newton-Raphson to find r_monthly such that annuity PV = 1
        r = flat_annual_rate / 12 * 1.8  # initial guess
        for _ in range(100):
            if r <= 0:
                r = 1e-6
            f  = pmt_per_unit * (1 - (1 + r) ** (-n)) / r - 1
            df = pmt_per_unit * (
                -((-n) * (1 + r) ** (-n - 1) * r - (1 - (1 + r) ** (-n))) / r ** 2
            )
            if abs(df) < 1e-12:
                break
            r_new = r - f / df
            if abs(r_new - r) < 1e-10:
                r = r_new
                break
            r = r_new
        return max(r, 0.0)


# ── Specialized Subclasses ────────────────────────────────────────────────────

@dataclass
class FlatRateDebt(DebtInstrument):
    """
    Thai-style personal loan / hire purchase with flat interest rate.
    Total interest = principal × flat_rate × years  (pre-calculated at signing).
    Monthly payment is FIXED from day 1.
    
    Key insight: current_balance ≠ accounting balance.
    We track RULE OF 78s or straight-line remaining balance.
    """
    use_rule_of_78: bool = True  # True = rule of 78s, False = straight-line

    def __post_init__(self):
        self.interest_type = InterestType.FLAT
        super().__post_init__()

    def _compute_monthly_payment(self) -> float:
        """
        Flat rate: fixed payment = (principal + total_interest) / n
        """
        n = self.original_term_months or 12
        T = n / 12
        total_repayment = self.original_balance * (1 + self.annual_rate * T)
        fixed_pmt = total_repayment / n
        return max(fixed_pmt, self.minimum_payment)

    def _remaining_balance_rule78(self) -> float:
        """
        Rule of 78s: earlier payments carry more interest.
        Calculates true remaining balance if user has made k payments.
        """
        n = self.original_term_months or 12
        T = n / 12
        total_interest = self.original_balance * self.annual_rate * T

        # Figure out how many payments have been made
        if self.remaining_term_months is not None:
            k = n - self.remaining_term_months
        else:
            # Estimate from current_balance vs original
            pmt = self._compute_monthly_payment()
            total_paid_approx = (self.original_balance - self.current_balance)
            k = max(0, round(total_paid_approx / pmt))

        # Sum of digits
        sum_all      = n * (n + 1) / 2
        sum_remaining = (n - k) * (n - k + 1) / 2
        unearned_interest = total_interest * (sum_remaining / sum_all)

        remaining_balance = (self.monthly_payment * (n - k)) - unearned_interest
        return max(remaining_balance, 0)

    def amortization_schedule(self) -> list[dict]:
        """
        Flat rate schedule: fixed payment each month,
        interest front-loaded via Rule of 78s.
        """
        n      = self.original_term_months or 12
        T      = n / 12
        total_interest = self.original_balance * self.annual_rate * T
        pmt    = self._compute_monthly_payment()
        sum_all = n * (n + 1) / 2

        # Determine starting month
        if self.remaining_term_months is not None:
            k_start = n - self.remaining_term_months
        else:
            k_start = 0

        schedule  = []
        balance   = self.current_balance
        cum_int   = 0.0

        for i in range(k_start, n):
            month_num      = i + 1
            digits_remain  = n - i  # digits for this period (rule of 78s)
            interest       = total_interest * (digits_remain / sum_all)
            principal      = pmt - interest
            balance       -= principal
            cum_int       += interest

            schedule.append({
                "month":               month_num - k_start,
                "balance_start":       round(balance + principal, 2),
                "interest_charge":     round(interest, 2),
                "principal_paid":      round(principal, 2),
                "payment":             round(pmt, 2),
                "balance_end":         round(max(balance, 0), 2),
                "cumulative_interest": round(cum_int, 2),
                "orbit_locked":        False,
            })
            if balance <= 0.01:
                break

        return schedule


@dataclass
class EffectiveRateDebt(DebtInstrument):
    """
    Standard amortizing loan (mortgage, car loan, personal loan with APR).
    Uses textbook annuity formula — base class handles this correctly.
    """
    def __post_init__(self):
        self.interest_type = InterestType.EFFECTIVE
        super().__post_init__()


@dataclass
class CompoundRateDebt(DebtInstrument):
    """
    Explicitly compound-rate debt (same math as effective, but user
    may specify compounding frequency).
    """
    compounding_frequency: int = 12  # times per year (12=monthly, 365=daily)

    def __post_init__(self):
        self.interest_type = InterestType.COMPOUND
        super().__post_init__()

    def _compute_effective_monthly_rate(self) -> float:
        """Convert nominal annual rate with compounding_frequency to monthly."""
        # EAR = (1 + r/m)^m - 1
        # Monthly = (1 + EAR)^(1/12) - 1
        m   = self.compounding_frequency
        ear = (1 + self.annual_rate / m) ** m - 1
        return (1 + ear) ** (1 / 12) - 1


@dataclass
class CreditCardDebt(DebtInstrument):
    """
    Revolving credit card debt.
    - Daily periodic rate applied to average daily balance
    - Minimum payment = max(fixed_floor, balance * min_pct)
    - New purchases can increase balance (modeled as static here)
    """
    min_payment_pct:   float = 0.05    # 5% of balance (common Thai bank default)
    min_payment_floor: float = 500.0   # minimum floor in ฿
    monthly_new_charges: float = 0.0   # new spending added each month

    def __post_init__(self):
        self.interest_type = InterestType.EFFECTIVE
        super().__post_init__()

    def _compute_monthly_payment(self) -> float:
        """
        Credit card minimum = max(floor, balance * pct).
        User can override with a higher fixed payment.
        """
        computed_min = max(
            self.min_payment_floor,
            self.current_balance * self.min_payment_pct
        )
        return max(computed_min, self.minimum_payment)

    def amortization_schedule(self) -> list[dict]:
        """Handles revolving balance with new monthly charges."""
        schedule  = []
        balance   = self.current_balance
        r         = self._effective_monthly_rate
        cum_int   = 0.0
        month     = 0

        while balance > 0.01 and month < 1200:
            month    += 1
            interest  = balance * r
            # Recompute minimum payment on current balance
            pmt = max(
                self.min_payment_floor,
                balance * self.min_payment_pct,
                self.minimum_payment
            )
            balance  += self.monthly_new_charges + interest - pmt
            cum_int  += interest
            principal = pmt - interest - self.monthly_new_charges

            schedule.append({
                "month":               month,
                "balance_start":       round(balance - self.monthly_new_charges - interest + pmt, 2),
                "interest_charge":     round(interest, 2),
                "new_charges":         round(self.monthly_new_charges, 2),
                "principal_paid":      round(max(principal, 0), 2),
                "payment":             round(pmt, 2),
                "balance_end":         round(max(balance, 0), 2),
                "cumulative_interest": round(cum_int, 2),
                "orbit_locked":        pmt <= interest + self.monthly_new_charges,
            })

            if balance <= 0:
                break
            if balance > self.current_balance * 100:
                # Runaway debt — hard stop
                break

        return schedule


@dataclass
class HirePurchaseDebt(DebtInstrument):
    """
    Thai hire-purchase (ผ่อนชำระ) — flat rate, fixed installments.
    Very common for vehicles, electronics.
    Essentially FlatRateDebt but with explicit remaining installments.
    """
    total_installments: int   = 0      # total number of payments
    paid_installments:  int   = 0      # how many already paid
    installment_amount: float = 0.0    # fixed monthly payment amount

    def __post_init__(self):
        self.interest_type = InterestType.FLAT
        if self.total_installments > 0:
            self.original_term_months  = self.total_installments
            self.remaining_term_months = self.total_installments - self.paid_installments
        if self.installment_amount > 0:
            self.minimum_payment = self.installment_amount
        super().__post_init__()

    def _compute_monthly_payment(self) -> float:
        if self.installment_amount > 0:
            return self.installment_amount
        return super()._compute_monthly_payment()


@dataclass
class BalloonDebt(DebtInstrument):
    """
    Balloon / bullet loan — pay interest only (or partial amortization)
    then a large lump sum at maturity.
    Common in: business loans, some Thai bank products.
    """
    balloon_amount:    float = 0.0    # lump sum due at end
    balloon_month:     int   = 0      # when balloon is due
    interest_only:     bool  = False  # if True, monthly payment = interest only

    def __post_init__(self):
        if self.balloon_amount <= 0:
            self.balloon_amount = self.current_balance or self.original_balance
        if self.balloon_month <= 0:
            self.balloon_month  = self.remaining_term_months or self.original_term_months or 12
        super().__post_init__()

    def _compute_monthly_payment(self) -> float:
        if self.interest_only:
            # Pure interest-only
            return max(self.current_balance * self._effective_monthly_rate,
                       self.minimum_payment)
        # Partial amortization — compute on non-balloon portion
        D_amort = self.current_balance - (
            self.balloon_amount / (1 + self._effective_monthly_rate) ** self.balloon_month
        )
        if D_amort <= 0:
            return max(self.current_balance * self._effective_monthly_rate,
                       self.minimum_payment)
        r = self._effective_monthly_rate
        n = self.balloon_month
        pmt = D_amort * r / (1 - (1 + r) ** (-n))
        return max(pmt, self.minimum_payment)

    def amortization_schedule(self) -> list[dict]:
        schedule  = []
        balance   = self.current_balance
        r         = self._effective_monthly_rate
        pmt       = self.monthly_payment
        cum_int   = 0.0

        for month in range(1, self.balloon_month + 1):
            interest  = balance * r
            if month == self.balloon_month:
                # Final balloon payment
                principal = balance
                actual_pmt = balance + interest
                balance   = 0
            else:
                principal  = max(pmt - interest, 0)
                actual_pmt = pmt
                balance   -= principal
            cum_int += interest

            schedule.append({
                "month":               month,
                "balance_start":       round(balance + principal, 2),
                "interest_charge":     round(interest, 2),
                "principal_paid":      round(principal, 2),
                "payment":             round(actual_pmt, 2),
                "balance_end":         round(max(balance, 0), 2),
                "cumulative_interest": round(cum_int, 2),
                "is_balloon":          month == self.balloon_month,
                "orbit_locked":        False,
            })

        return schedule
