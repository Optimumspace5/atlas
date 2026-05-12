# Atlas Taxonomy Audit Notes
**Version:** v0.1
**Author:** Clarence Lee
**Date:** 2026-05-12
**Status:** In Progress — 5-book initial audit complete, 10–15 book expanded audit pending

---

## Purpose

This document records the findings from auditing `taxonomy_v0.1.yaml` against real investing and trading books. It tracks:
- Leaf concept mappings per book
- Weak tags flagged for confirmation during the full audit
- Taxonomy gaps identified during the audit
- Final freeze decisions made at EOD

---

## Audit Rules

- Tag conservatively. A book must meaningfully cover a concept, not just mention it.
- Maximum ~8–10 leaf tags per book. Exceeding this signals over-tagging.
- Flag every leaf that feels missing, vague, or overlapping — do not silently skip.
- Weak tags must be confirmed during the expanded audit before being included in the frozen taxonomy.

---

## 5-Book Initial Audit

### Book 1: Technical Analysis of the Financial Markets — John J. Murphy

**Category:** Technical Analysis anchor book
**Total tags:** 7 confirmed, 1 weak

**Confirmed mappings:**
```yaml
technical_analysis_of_the_financial_markets:
  technical_analysis_and_market_structure:
    - trend_analysis
    - support_and_resistance
    - chart_patterns_and_price_action
    - volume_analysis
    - momentum_and_technical_indicators
    - market_structure_and_regime_identification
  strategy_systems_and_execution:
    - strategy_design_and_rule_definition
  market_foundations:
    - order_types_and_execution_mechanics   # weak — confirm during full audit
```

**Weak tags:**
- `order_types_and_execution_mechanics` — Murphy covers order types and basic order placement as part of trading tactics, but this is not a primary focus. Confirm during expanded audit.

**Excluded and why:**
- `execution_quality_and_trade_implementation` — Murphy's tactics chapter covers trade application, not execution mechanics like slippage, fills, or spread costs.
- `backtesting_and_strategy_validation` — trading systems coverage does not mean historical testing, overfitting, or walk-forward validation.
- `position_sizing` / `stop_loss_and_exit_rules` — possible, but requires manual confirmation that sizing or stop rules are taught in a reusable, meaningful way.

**Taxonomy gaps flagged:** None

---

### Book 2: The Intelligent Investor — Benjamin Graham

**Category:** Value investing + investor discipline anchor book
**Total tags:** 9 confirmed, 2 weak

**Confirmed mappings:**
```yaml
the_intelligent_investor:
  fundamental_analysis_and_valuation:
    - financial_statement_analysis          # weak — confirm during full audit
    - intrinsic_value_estimation
    - valuation_multiples_and_relative_valuation
    - margin_of_safety
  portfolio_construction_and_asset_allocation:
    - asset_allocation
    - diversification
    - passive_vs_active_portfolio_management
    - time_horizon_and_goal_based_investing  # weak — confirm during full audit
  macro_cycles_and_economic_context:
    - inflation_and_purchasing_power
  trading_psychology_and_behavioral_finance:
    - emotional_discipline
    - process_oriented_decision_making
```

**Weak tags:**
- `financial_statement_analysis` — The Intelligent Investor operates at the level of ratios and summary metrics, not deep statement analysis. That is Security Analysis (Graham's other book). Confirm during expanded audit.
- `time_horizon_and_goal_based_investing` — Graham structures the book around defensive vs enterprising investor profiles, which implies time horizon and risk tolerance distinctions, but this is not the primary framing. Confirm during expanded audit.

**Excluded and why:**
- `business_quality_and_competitive_advantage` — Graham cares about financial soundness and earnings stability, not moats or competitive advantage in the modern Buffett/Munger sense.
- `earnings_growth_and_profitability_drivers` — The book emphasizes conservative valuation and margin of safety, not reinvestment, ROIC, or operating leverage analysis.
- `cognitive_biases` — Graham predates behavioral finance as a field. Tagging it would be anachronistic. His discussion of temperament and speculation is captured by `emotional_discipline`.

**Taxonomy gaps flagged:**
- `investment_vs_speculation` — Central concept in The Intelligent Investor. Does not fit perfectly under any existing leaf. Currently absorbed under `process_oriented_decision_making`. **Hold — do not add yet. Confirm if additional books expose the same gap.**

---

### Book 3: The Psychology of Money — Morgan Housel

**Category:** Behavioral finance + long-term wealth psychology book
**Total tags:** 7 confirmed

**Confirmed mappings:**
```yaml
the_psychology_of_money:
  trading_psychology_and_behavioral_finance:
    - cognitive_biases
    - emotional_discipline
    - loss_aversion_and_drawdown_psychology
    - probabilistic_thinking_and_uncertainty
    - process_oriented_decision_making
    - overtrading_and_impulse_control
  portfolio_construction_and_asset_allocation:
    - time_horizon_and_goal_based_investing
```

**Excluded and why:**
- `risk_return_tradeoff` — Housel discusses risk in a personal and behavioral sense, not in the portfolio theory sense the leaf definition specifies (volatility, Sharpe ratio, efficient frontier). The behavioral dimension is already captured by `loss_aversion_and_drawdown_psychology` and `probabilistic_thinking_and_uncertainty`.
- `portfolio_rebalancing` — Not meaningfully covered. Do not tag without manual confirmation.
- `passive_vs_active_portfolio_management` — Touched on but not developed as a primary topic.
- `inflation_and_purchasing_power` — Appears as an example of how lived experience shapes beliefs, not as a developed macro concept.
- `asset_allocation` — Not meaningfully taught as a portfolio construction concept.

**Taxonomy gaps flagged:**
- `financial_resilience_and_saving` — Housel spends meaningful attention on saving behavior, room for error, staying wealthy, and financial independence. No existing leaf captures "build slack so you can survive uncertainty" cleanly. Currently absorbed across `risk_return_tradeoff`, `time_horizon_and_goal_based_investing`, and `loss_aversion_and_drawdown_psychology`. **Hold — do not add yet. Revisit if more books expose the same gap.**

---

### Book 4: Trading in the Zone — Mark Douglas

**Category:** Pure trading psychology + probabilistic mindset book
**Total tags:** 5 confirmed

**Confirmed mappings:**
```yaml
trading_in_the_zone:
  trading_psychology_and_behavioral_finance:
    - emotional_discipline
    - cognitive_biases
    - loss_aversion_and_drawdown_psychology
    - process_oriented_decision_making
    - probabilistic_thinking_and_uncertainty
```

**Excluded and why:**
- `strategy_design_and_rule_definition` — Douglas explicitly assumes the trader already has a system or edge. The book is about mindset, not designing trading rules.
- `stop_loss_and_exit_rules` — The book discusses accepting risk psychologically, but does not teach stop-loss placement or exit-rule design.
- `risk_reward_and_expectancy` — Probabilistic thinking and expectancy math are related but distinct. The book does not teach win-rate or payoff calculations.
- `overtrading_and_impulse_control` — Possible, but not confirmed. Do not tag without manual audit confirmation.

**Cross-book observation:**
Trading in the Zone and The Psychology of Money share 4 leaf tags (`emotional_discipline`, `cognitive_biases`, `loss_aversion_and_drawdown_psychology`, `process_oriented_decision_making`). This is expected behavior, not a taxonomy problem. It confirms these two books are highly redundant for a user who has read one of them. Atlas's redundancy avoidance logic should penalize recommending both to the same user.

**Taxonomy gaps flagged:**
- `risk_acceptance` — Douglas spends significant attention on emotionally accepting risk before entering a trade. Partially captured by `loss_aversion_and_drawdown_psychology` and `probabilistic_thinking_and_uncertainty`, but neither is a perfect fit. **Hold — do not add yet. Confirm if psychology-heavy trading books consistently expose the same gap.**

---

### Book 5: Big Debt Crises — Ray Dalio

**Category:** Macro debt-cycle + crisis framework book
**Total tags:** 6 confirmed

**Confirmed mappings:**
```yaml
big_debt_crises:
  macro_cycles_and_economic_context:
    - credit_debt_and_financial_crises
    - business_cycles_and_market_cycles
    - interest_rates_and_monetary_policy
    - inflation_and_purchasing_power
    - currency_and_global_macro_forces
    - economic_indicators_and_data_interpretation
```

**Excluded and why:**
- `leverage_and_margin_risk` — The book discusses leverage at the economy and system level. The leaf is defined at the position and account level (margin calls, liquidation risk, leveraged instruments). These are different scopes.
- `asset_allocation` — Dalio implies that different macro environments affect different assets, but this book does not teach portfolio construction or allocation rules.
- `market_participants_and_incentives` — The book discusses borrowers, lenders, policy makers, and central banks, but the purpose is to explain macro debt dynamics, not general market participant mechanics.

**Taxonomy gaps flagged:**
- `policy_response_and_crisis_management` — Dalio spends significant attention on how policy makers use levers such as austerity, debt restructuring, money printing, and transfers to manage debt crises. Partially captured by `interest_rates_and_monetary_policy` and `credit_debt_and_financial_crises`, but not completely. **Hold — do not add yet. Confirm if more macro books expose the same gap.**

---

## Taxonomy Gap Tracker

| Gap candidate | First flagged by | Status | Decision |
|---|---|---|---|
| `investment_vs_speculation` | The Intelligent Investor | Hold | Absorbed under `process_oriented_decision_making` for now |
| `financial_resilience_and_saving` | The Psychology of Money | Hold | Revisit if more books expose it |
| `risk_acceptance` | Trading in the Zone | Hold | Revisit if psychology books consistently expose it |
| `policy_response_and_crisis_management` | Big Debt Crises | Hold | Revisit if more macro books expose it |

---

## Weak Tag Tracker

| Weak tag | Book | Resolution |
|---|---|---|
| `order_types_and_execution_mechanics` | Technical Analysis of the Financial Markets | Confirm during expanded audit |
| `financial_statement_analysis` | The Intelligent Investor | Confirm during expanded audit |
| `time_horizon_and_goal_based_investing` | The Intelligent Investor | Confirm during expanded audit |

---

## Overall Taxonomy Health After 5-Book Audit

- **No missing parent categories** — all 5 books mapped cleanly within the 8 existing categories
- **No overlapping leaves requiring merges** — leaves that appeared related (trend analysis, support/resistance, market structure) proved separable in practice
- **No leaf proved impossible to tag against** — every concept node was usable
- **4 gap candidates identified** — all held pending confirmation from expanded audit
- **3 weak tags flagged** — pending manual confirmation

**Verdict: taxonomy structure holds. Proceed to expanded 10–15 book audit.**

---

## Expanded Audit (10–15 Books) — Pending

*To be completed EOD Tuesday 12 May 2026.*

Books to audit:

- [ ] TBD
- [ ] TBD
- [ ] TBD
- [ ] TBD
- [ ] TBD
- [ ] TBD
- [ ] TBD
- [ ] TBD
- [ ] TBD
- [ ] TBD

Findings to be documented here after audit is complete.

---

## Freeze Decision Log

*To be completed EOD Tuesday 12 May 2026.*

| Decision | Rationale |
|---|---|
| TBD | TBD |
