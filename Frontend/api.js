/**
 * api.js
 * ──────
 * JavaScript client for the Debt → Portfolio → Model V.2 Flask API.
 *
 * Usage (in any HTML file):
 *   <script src="api.js"></script>
 *   <script>
 *     const api = new DebtAPI();
 *     const result = await api.analyzePortfolio(payload);
 *   </script>
 *
 * All methods return parsed JSON on success.
 * On error they throw an Error with a human-readable message.
 */

class DebtAPI {
  /**
   * @param {string} baseUrl  - Flask server base URL (default: http://localhost:5000)
   */
  constructor(baseUrl = "http://localhost:5000") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  // ── Internal fetch helper ──────────────────────────────────────────────────

  async _post(path, body) {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const json = await res.json();
    if (!res.ok) {
      throw new Error(json.error || `HTTP ${res.status}`);
    }
    return json;
  }

  async _get(path) {
    const res = await fetch(`${this.baseUrl}${path}`);
    const json = await res.json();
    if (!res.ok) {
      throw new Error(json.error || `HTTP ${res.status}`);
    }
    return json;
  }

  // ── Health ─────────────────────────────────────────────────────────────────

  /** Ping the server. Returns { status: "ok" } */
  async health() {
    return this._get("/api/health");
  }

  // ── Debt types reference ───────────────────────────────────────────────────

  /**
   * Get all supported debt types, their required/optional fields, and purpose enum values.
   * Useful for dynamically building forms.
   * @returns {{ debt_types: object, purposes: string[] }}
   */
  async getDebtTypes() {
    return this._get("/api/debt/types");
  }

  // ── Portfolio ──────────────────────────────────────────────────────────────

  /**
   * Compute portfolio aggregates only (D, r, P, breakdown).
   * Use when you only need the portfolio roll-up without model layers.
   *
   * @param {DebtObject[]} debts
   * @returns {PortfolioSummary}
   *
   * @example
   * const summary = await api.getPortfolioSummary([
   *   { type: "credit_card", name: "KBank", current_balance: 45000, annual_rate: 0.18 }
   * ]);
   */
  async getPortfolioSummary(debts) {
    return this._post("/api/portfolio/summary", { debts });
  }

  /**
   * Full pipeline: debts → portfolio → Layer 1 + Layer 2 + Layer 3.
   * This is the main endpoint for a complete dashboard.
   *
   * @param {AnalyzePayload} payload
   * @returns {{ portfolio, layer1, layer2, layer3 }}
   *
   * @example
   * const result = await api.analyzePortfolio({
   *   debts: [ ... ],
   *   income: 120000,
   *   necessary_expenses: 35000,
   *   discretionary_expenses: 15000,
   *   behavior_score: 4,
   *   emergency_fund: 90000,
   *   months_on_budget: 4,
   *   target_freedom_months: 60,
   *   current_age: 38,
   *   career_type: "technical_engineering",
   *   t_start: 22,
   *   t_retire: 60,
   * });
   */
  async analyzePortfolio(payload) {
    return this._post("/api/portfolio/analyze", payload);
  }

  // ── Individual layers ──────────────────────────────────────────────────────

  /**
   * Layer 1 only — Three Forces, orbital zone, escape score, t*, momentum.
   *
   * @param {object} params  - Must include debts[] + income/expense/behavior fields
   * @returns {Layer1Result}
   */
  async getLayer1(params) {
    return this._post("/api/layer1", params);
  }

  /**
   * Layer 2 only — HCDF, LDER, lifetime trajectory.
   *
   * @param {object} params  - Layer 1 fields + current_age, career_type, t_start, t_retire
   * @returns {Layer2Result}
   */
  async getLayer2(params) {
    return this._post("/api/layer2", params);
  }

  /**
   * Layer 3 only — Debt quality / VM badge (slingshot / neutral / drag).
   *
   * @param {DebtObject[]} debts
   * @returns {Layer3Result}
   */
  async getLayer3(debts) {
    return this._post("/api/layer3", { debts });
  }

  // ── Single debt tools ──────────────────────────────────────────────────────

  /**
   * Get the amortization schedule for one debt.
   *
   * @param {DebtObject} debt
   * @param {number} [months=0]  - Number of rows to return (0 = full schedule)
   * @returns {{ summary: object, schedule: AmortRow[] }}
   *
   * @example
   * const { schedule } = await api.getAmortization(
   *   { type: "hire_purchase", name: "Toyota", current_balance: 360000,
   *     annual_rate: 0.03, total_installments: 60, paid_installments: 24,
   *     installment_amount: 11500 },
   *   6   // first 6 months
   * );
   */
  async getAmortization(debt, months = 0) {
    return this._post("/api/debt/amortization", { debt, months });
  }

  /**
   * Get a full payoff quote for a single debt (includes all fees).
   *
   * @param {DebtObject} debt
   * @param {object} [options]
   * @param {"mortgage"|"personal_loan"|"cooperative"|"none"} [options.feePreset="none"]
   * @param {string} [options.signDate]   - ISO date "YYYY-MM-DD"
   * @param {string} [options.asOfDate]   - ISO date "YYYY-MM-DD" (defaults to today)
   * @returns {PayoffResult}
   *
   * @example
   * const quote = await api.getPayoffQuote(
   *   { type: "effective", name: "GH Bank Mortgage", current_balance: 2400000,
   *     annual_rate: 0.065, remaining_term_months: 180 },
   *   { feePreset: "mortgage", signDate: "2020-01-01" }
   * );
   */
  async getPayoffQuote(debt, { feePreset = "none", signDate = null, asOfDate = null } = {}) {
    return this._post("/api/debt/payoff", {
      debt,
      fee_preset: feePreset,
      sign_date:  signDate,
      as_of_date: asOfDate,
    });
  }
}


// ── Debt builder helpers ───────────────────────────────────────────────────────
// Convenience factory functions so callers don't have to remember field names.

const DebtBuilder = {

  /**
   * Standard amortizing loan (mortgage, personal loan, car loan with APR).
   */
  effective({ name, creditor = "", purpose = "other", currentBalance, annualRate,
              originalBalance = null, originalTermMonths = null,
              remainingTermMonths = null, minimumPayment = 0 }) {
    return {
      type: "effective", name, creditor, purpose,
      current_balance: currentBalance,
      annual_rate: annualRate,
      original_balance: originalBalance ?? currentBalance,
      original_term_months: originalTermMonths,
      remaining_term_months: remainingTermMonths,
      minimum_payment: minimumPayment,
    };
  },

  /**
   * Credit card revolving debt.
   */
  creditCard({ name, creditor = "", currentBalance, annualRate = 0.18,
               minimumPayment = 0, minPaymentPct = 0.05,
               minPaymentFloor = 500, monthlyNewCharges = 0 }) {
    return {
      type: "credit_card", name, creditor,
      purpose: "credit_card",
      current_balance: currentBalance,
      annual_rate: annualRate,
      minimum_payment: minimumPayment,
      min_payment_pct: minPaymentPct,
      min_payment_floor: minPaymentFloor,
      monthly_new_charges: monthlyNewCharges,
    };
  },

  /**
   * Thai hire-purchase (ผ่อนชำระ).
   */
  hirePurchase({ name, creditor = "", purpose = "auto",
                 originalBalance, currentBalance, annualRate,
                 totalInstallments, paidInstallments, installmentAmount }) {
    return {
      type: "hire_purchase", name, creditor, purpose,
      original_balance: originalBalance,
      current_balance: currentBalance,
      annual_rate: annualRate,
      total_installments: totalInstallments,
      paid_installments: paidInstallments,
      installment_amount: installmentAmount,
    };
  },

  /**
   * Flat-rate loan (Thai personal loan style).
   */
  flat({ name, creditor = "", purpose = "personal",
         originalBalance, currentBalance, annualRate,
         originalTermMonths, remainingTermMonths = null,
         minimumPayment = 0, useRuleOf78 = true }) {
    return {
      type: "flat", name, creditor, purpose,
      original_balance: originalBalance,
      current_balance: currentBalance,
      annual_rate: annualRate,
      original_term_months: originalTermMonths,
      remaining_term_months: remainingTermMonths,
      minimum_payment: minimumPayment,
      use_rule_of_78: useRuleOf78,
    };
  },

  /**
   * Balloon / bullet loan.
   */
  balloon({ name, creditor = "", purpose = "business",
            currentBalance, annualRate, balloonMonth,
            balloonAmount = null, interestOnly = false, minimumPayment = 0 }) {
    return {
      type: "balloon", name, creditor, purpose,
      current_balance: currentBalance,
      annual_rate: annualRate,
      balloon_month: balloonMonth,
      balloon_amount: balloonAmount ?? currentBalance,
      interest_only: interestOnly,
      minimum_payment: minimumPayment,
    };
  },

  /**
   * Step-up / promotional rate mortgage.
   * @param {Array<{monthsDuration, annualRate, label}>} rateSchedule
   */
  stepUp({ name, creditor = "", purpose = "mortgage",
           currentBalance, originalBalance = null,
           originalTermMonths = null, monthsElapsed = 0,
           rateSchedule, minimumPayment = 0 }) {
    return {
      type: "step_up", name, creditor, purpose,
      current_balance: currentBalance,
      original_balance: originalBalance ?? currentBalance,
      original_term_months: originalTermMonths,
      months_elapsed: monthsElapsed,
      minimum_payment: minimumPayment,
      rate_schedule: rateSchedule.map(p => ({
        months_duration: p.monthsDuration,
        annual_rate: p.annualRate,
        label: p.label ?? "",
      })),
    };
  },

  /**
   * MRR/MLR floating rate debt.
   */
  floating({ name, creditor = "", purpose = "mortgage",
             currentBalance, remainingTermMonths = null,
             referenceRate = 0.0725, spread = -0.015,
             referenceName = "MRR", minimumPayment = 0 }) {
    return {
      type: "floating", name, creditor, purpose,
      current_balance: currentBalance,
      remaining_term_months: remainingTermMonths,
      reference_rate: referenceRate,
      spread,
      reference_name: referenceName,
      minimum_payment: minimumPayment,
    };
  },

  /**
   * กยศ. annual step-up student loan.
   */
  annualStepUp({ name, creditor = "กยศ.", purpose = "education",
                 currentBalance, originalBalance = null,
                 originalTermMonths = null, remainingTermMonths = null,
                 initialRate = 0.01, stepRate = 0.01, maxRate = 0.01,
                 yearsElapsed = 0, graceMonths = 0, simpleInterest = true }) {
    return {
      type: "annual_step_up", name, creditor, purpose,
      current_balance: currentBalance,
      original_balance: originalBalance ?? currentBalance,
      original_term_months: originalTermMonths,
      remaining_term_months: remainingTermMonths,
      initial_rate: initialRate,
      step_rate: stepRate,
      max_rate: maxRate,
      years_elapsed: yearsElapsed,
      grace_months: graceMonths,
      simple_interest: simpleInterest,
    };
  },

  /**
   * สหกรณ์ออมทรัพย์ cooperative loan.
   */
  cooperative({ name, creditor = "", purpose = "personal",
                currentBalance, loanRate, shareSubscription,
                monthlyShareDeposit = 0, dividendYield = 0.065,
                remainingTermMonths = null, minimumPayment = 0 }) {
    return {
      type: "cooperative", name, creditor, purpose,
      current_balance: currentBalance,
      annual_rate: loanRate,
      loan_rate: loanRate,
      share_subscription: shareSubscription,
      monthly_share_deposit: monthlyShareDeposit,
      dividend_yield: dividendYield,
      remaining_term_months: remainingTermMonths,
      minimum_payment: minimumPayment,
    };
  },
};


// ── Display / formatting helpers ──────────────────────────────────────────────

const DebtFormat = {

  /** Format a Thai Baht amount: ฿1,234,567.89 */
  baht(amount, decimals = 0) {
    if (amount === null || amount === undefined) return "—";
    return "฿" + Number(amount).toLocaleString("th-TH", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  },

  /** Format a percentage: 6.50% */
  pct(rate, decimals = 2) {
    if (rate === null || rate === undefined) return "—";
    return Number(rate).toFixed(decimals) + "%";
  },

  /** Zone emoji + label from layer1 result */
  zoneLabel(layer1) {
    return layer1.zone_label ?? layer1.zone ?? "—";
  },

  /** LDER status label from layer2 result */
  lderLabel(layer2) {
    return layer2.lder_label ?? layer2.lder_status ?? "—";
  },

  /** VM badge from layer3 result */
  vmBadge(layer3) {
    return layer3.debt_type_badge ?? "—";
  },

  /**
   * Render a simple amortization table into a <table> element.
   * @param {HTMLTableElement} tableEl
   * @param {AmortRow[]} schedule
   */
  renderAmortTable(tableEl, schedule) {
    tableEl.innerHTML = "";
    const thead = tableEl.createTHead();
    const hrow  = thead.insertRow();
    ["Month", "Balance (start)", "Interest", "Principal", "Payment", "Balance (end)"]
      .forEach(h => {
        const th = document.createElement("th");
        th.textContent = h;
        hrow.appendChild(th);
      });

    const tbody = tableEl.createTBody();
    schedule.forEach(row => {
      const tr = tbody.insertRow();
      [
        row.month,
        DebtFormat.baht(row.balance_start),
        DebtFormat.baht(row.interest_charge),
        DebtFormat.baht(row.principal_paid),
        DebtFormat.baht(row.payment),
        DebtFormat.baht(row.balance_end),
      ].forEach(val => {
        const td = tr.insertCell();
        td.textContent = val;
        if (row.orbit_locked) td.style.color = "red";
      });
    });
  },

  /**
   * Summarize a full /api/portfolio/analyze response into a flat display object.
   * Useful for binding to a UI template.
   */
  summarizeAnalysis(result) {
    const { portfolio, layer1, layer2, layer3 } = result;
    return {
      // Portfolio
      totalDebt:       DebtFormat.baht(portfolio.D),
      weightedRate:    DebtFormat.pct(portfolio.r * 100),
      totalPayment:    DebtFormat.baht(portfolio.P),
      monthlyInterest: DebtFormat.baht(portfolio.monthly_interest_total),
      monthlyPrincipal:DebtFormat.baht(portfolio.monthly_principal_total),
      numDebts:        portfolio.num_debts,
      orbitLocked:     portfolio.is_orbit_locked,

      // Layer 1
      zone:            DebtFormat.zoneLabel(layer1),
      fNet:            DebtFormat.baht(layer1.F_net),
      escapeScore:     layer1.S_E?.toFixed(1) ?? "—",
      timeToFreedom:   layer1.t_star_display,
      gap:             DebtFormat.baht(layer1.delta),
      gapMessage:      layer1.gap_message,
      momentum:        layer1.momentum_display,
      momentumWarning: layer1.momentum_warning,

      // Layer 2
      hcdf:            DebtFormat.pct(layer2.HCDF * 100),
      lder:            layer2.LDER?.toFixed(3) ?? "∞",
      lderStatus:      DebtFormat.lderLabel(layer2),
      tehWarning:      layer2.teh_warning,

      // Layer 3
      vmBadge:         DebtFormat.vmBadge(layer3),
      dragRatio:       DebtFormat.pct(layer3.drag_ratio_pct),
      slingshotBalance:DebtFormat.baht(layer3.slingshot_balance),
      dragBalance:     DebtFormat.baht(layer3.drag_balance),
    };
  },
};


// ── Demo usage (runs when loaded directly, not when imported as module) ────────

async function runDemo() {
  const api = new DebtAPI();

  // ── Build debts using the helper ──────────────────────────────────────────
  const debts = [
    DebtBuilder.creditCard({
      name: "Kasikorn Credit Card",
      creditor: "KBank",
      currentBalance: 45000,
      annualRate: 0.18,
      minimumPayment: 2500,
    }),
    DebtBuilder.hirePurchase({
      name: "Toyota Vios (Hire Purchase)",
      creditor: "Toyota Leasing",
      originalBalance: 600000,
      currentBalance: 360000,
      annualRate: 0.03,
      totalInstallments: 60,
      paidInstallments: 24,
      installmentAmount: 11500,
    }),
    DebtBuilder.effective({
      name: "SCB Personal Loan",
      creditor: "SCB",
      purpose: "personal",
      originalBalance: 150000,
      currentBalance: 80000,
      annualRate: 0.12,
      originalTermMonths: 36,
      remainingTermMonths: 18,
      minimumPayment: 5000,
    }),
    DebtBuilder.effective({
      name: "Home Mortgage — GH Bank",
      creditor: "Government Housing Bank",
      purpose: "mortgage",
      originalBalance: 3000000,
      currentBalance: 2400000,
      annualRate: 0.065,
      originalTermMonths: 240,
      remainingTermMonths: 180,
      minimumPayment: 18000,
    }),
  ];

  const payload = {
    debts,
    income: 120000,
    necessary_expenses: 35000,
    discretionary_expenses: 15000,
    behavior_score: 4,
    emergency_fund: 90000,
    months_on_budget: 4,
    target_freedom_months: 60,
    current_age: 38,
    career_type: "technical_engineering",
    t_start: 22,
    t_retire: 60,
  };

  try {
    // ── Full analysis ───────────────────────────────────────────────────────
    const result  = await api.analyzePortfolio(payload);
    const display = DebtFormat.summarizeAnalysis(result);

    console.group("📊 Portfolio Analysis");
    console.log("Total debt:",       display.totalDebt);
    console.log("Weighted rate:",    display.weightedRate);
    console.log("Monthly payment:",  display.totalPayment);
    console.log("Zone:",             display.zone);
    console.log("Escape score:",     display.escapeScore);
    console.log("Time to freedom:",  display.timeToFreedom);
    console.log("Gap message:",      display.gapMessage);
    console.log("LDER:",             display.lder, "→", display.lderStatus);
    console.log("VM Badge:",         display.vmBadge);
    console.groupEnd();

    // ── Amortization for the credit card (first 6 months) ──────────────────
    const { schedule } = await api.getAmortization(debts[0], 6);
    console.group("💳 Credit Card — First 6 months");
    schedule.forEach(r =>
      console.log(`Month ${r.month}: interest=${DebtFormat.baht(r.interest_charge)}  principal=${DebtFormat.baht(r.principal_paid)}  balance=${DebtFormat.baht(r.balance_end)}`)
    );
    console.groupEnd();

    // ── Payoff quote for the mortgage ───────────────────────────────────────
    const quote = await api.getPayoffQuote(debts[3], {
      feePreset: "mortgage",
      signDate:  "2020-01-01",
    });
    console.group("🏠 Mortgage Payoff Quote");
    quote.fee_breakdown.forEach(item =>
      console.log(`  ${item[0]}: ฿${Number(item[1]).toLocaleString()}`)
    );
    console.log("TOTAL CASH TO CLOSE:", DebtFormat.baht(quote.total_payoff));
    console.groupEnd();

  } catch (err) {
    console.error("API error:", err.message);
  }
}

// Uncomment to auto-run the demo when this script is loaded:
// runDemo();