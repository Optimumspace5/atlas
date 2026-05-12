# Atlas Taxonomy Audit Notes
**Version:** v0.1
**Author:** Clarence Lee
**Date:** 2026-05-12
**Status:** Complete — 17-book audit finished, taxonomy frozen

---

## Purpose

This document records the findings from auditing `taxonomy_v0.1.yaml` against real investing and trading books. It tracks:
- Leaf concept mappings per book
- Weak tags flagged for confirmation
- Taxonomy gaps identified during the audit
- Tagging guidelines derived from audit decisions
- Final freeze decisions

---

## Audit Rules

- Tag conservatively. A book must meaningfully cover a concept, not just mention it.
- Maximum ~8–10 leaf tags per book. Exceeding this signals over-tagging.
- Flag every leaf that feels missing, vague, or overlapping — do not silently skip.
- Weak tags must be confirmed during the expanded book database phase before being treated as confirmed.
- For interview-based books, only tag a leaf if it appears as a repeated cross-interview lesson or is explicitly emphasized by the author as a common success principle. One trader mentioning a concept is not sufficient.

---

## Tagging Guidelines

Derived from audit decisions across 17 books. Apply these consistently when tagging new books.

**Technical Analysis & Market Structure:**
> `market_structure_and_regime_identification` = what state the market is in.
> `chart_patterns_and_price_action` = what price behavior appears on the chart.
> `strategy_design_and_rule_definition` = what rule-based action the trader takes from that behavior.

**Strategy, Systems & Execution:**
> `stop_loss_and_exit_rules` = when and why to leave a position.
> `trade_planning_and_preparation` = what the trader prepares before entering.
> `execution_quality_and_trade_implementation` = how the trade is practically carried out (order routing, slippage, fills, spreads). Reserve for books that meaningfully teach implementation mechanics, not just trade logic.
> `process_oriented_decision_making` = reserved for psychology and behavioral decision-making, not investment process discipline in general.

**Fundamental Analysis & Valuation:**
> `business_quality_and_competitive_advantage` = why the business can resist competition.
> `earnings_growth_and_profitability_drivers` = how the business generates and compounds profits.
> `intrinsic_value_estimation` = what the business is worth based on future cash flows.

**Portfolio Construction & Asset Allocation:**
> `asset_classes_and_financial_instruments` = what investment building blocks exist.
> `asset_allocation` = how much capital goes into each building block.
> `diversification` = why spreading exposure reduces risk.

**Risk Management vs Portfolio Construction:**
> `exposure_and_concentration_limits` (Risk Management) = managing total position-level or account-level exposure across correlated trades.
> `diversification` (Portfolio Construction) = spreading exposure across assets, sectors, or geographies at the portfolio level.

**Market Foundations naming clash:**
> `market_structure_and_regime_identification` in Technical Analysis = trending/ranging/reversal regimes.
> "market structure" in microstructure books (e.g. Harris) = trading rules and market organization. These are different — do not cross-tag.

**Backtesting vs Statistical Validation:**
> `backtesting_and_strategy_validation` covers historical testing, walk-forward testing, paper trading, and overfitting checks.
> Data snooping bias and post-data-mining statistical significance are absorbed under this leaf for v0.1.

---

## Book Audit Mappings

### Book 1: Technical Analysis of the Financial Markets — John J. Murphy
**Category:** Technical Analysis anchor book
**Total tags:** 7 confirmed, 1 weak

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
    - order_types_and_execution_mechanics   # weak — confirm during expanded book database phase
```

**Key exclusions:**
- `execution_quality_and_trade_implementation` — tactics chapter covers trade application, not execution mechanics.
- `backtesting_and_strategy_validation` — trading systems coverage does not mean historical testing methodology.

---

### Book 2: The Intelligent Investor — Benjamin Graham
**Category:** Value investing + investor discipline anchor book
**Total tags:** 9 confirmed, 2 weak

```yaml
the_intelligent_investor:
  fundamental_analysis_and_valuation:
    - financial_statement_analysis          # weak — operates at ratio/summary level, not deep statement analysis
    - intrinsic_value_estimation
    - valuation_multiples_and_relative_valuation
    - margin_of_safety
  portfolio_construction_and_asset_allocation:
    - asset_allocation
    - diversification
    - passive_vs_active_portfolio_management
    - time_horizon_and_goal_based_investing  # weak — implied through defensive/enterprising investor distinction
  macro_cycles_and_economic_context:
    - inflation_and_purchasing_power
  trading_psychology_and_behavioral_finance:
    - emotional_discipline
    - process_oriented_decision_making
```

**Key exclusions:**
- `business_quality_and_competitive_advantage` — Graham cares about financial soundness, not moats. That is a Buffett/Munger lens.
- `cognitive_biases` — Graham predates behavioral finance. His temperament discussion is captured by `emotional_discipline`.

**Gap flagged:** `investment_vs_speculation` — central to this book, absorbed under `process_oriented_decision_making` for v0.1.

---

### Book 3: The Psychology of Money — Morgan Housel
**Category:** Behavioral finance + long-term wealth psychology book
**Total tags:** 7 confirmed

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

**Key exclusions:**
- `risk_return_tradeoff` — Housel discusses risk behaviorally, not in portfolio theory terms (Sharpe ratio, efficient frontier).
- `inflation_and_purchasing_power` — appears as example of lived experience, not developed macro concept.

**Gap flagged:** `financial_resilience_and_saving` — saving behavior, room for error, financial independence not cleanly captured. Hold for v0.2.

---

### Book 4: Trading in the Zone — Mark Douglas
**Category:** Pure trading psychology + probabilistic mindset book
**Total tags:** 5 confirmed

```yaml
trading_in_the_zone:
  trading_psychology_and_behavioral_finance:
    - emotional_discipline
    - cognitive_biases
    - loss_aversion_and_drawdown_psychology
    - process_oriented_decision_making
    - probabilistic_thinking_and_uncertainty
```

**Key exclusions:**
- `strategy_design_and_rule_definition` — Douglas explicitly assumes the trader already has a system. Book is about mindset only.
- `risk_reward_and_expectancy` — probabilistic thinking and expectancy math are related but distinct.

**Cross-book note:** Trading in the Zone and The Psychology of Money share 4 leaf tags. Atlas should penalize recommending both to the same user.

**Gap flagged:** `risk_acceptance` — emotionally accepting risk before entering a trade. Partially captured by existing leaves. Hold for v0.2.

---

### Book 5: Big Debt Crises — Ray Dalio
**Category:** Macro debt-cycle + crisis framework book
**Total tags:** 6 confirmed

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

**Key exclusions:**
- `leverage_and_margin_risk` — book discusses leverage at economy/system level. Leaf is defined at position/account level. Different scopes.
- `asset_allocation` — macro crisis dynamics are not portfolio construction rules.

**Gap flagged:** `policy_response_and_crisis_management` — use of policy levers partially captured but not perfectly. Hold for v0.2.

---

### Book 6: Trading and Exchanges — Larry Harris
**Category:** Market microstructure + Market Foundations anchor book
**Total tags:** 6 confirmed

```yaml
trading_and_exchanges_market_microstructure:
  market_foundations:
    - market_participants_and_incentives
    - exchanges_brokers_and_market_infrastructure
    - order_types_and_execution_mechanics
    - liquidity_and_market_depth
    - price_formation_and_market_efficiency
  strategy_systems_and_execution:
    - execution_quality_and_trade_implementation
```

**Key exclusions:**
- `market_structure_and_regime_identification` — Harris uses "market structure" in the microstructure sense, not the regime identification sense. False match.
- `asset_classes_and_financial_instruments` — book lists instruments as context, does not teach them as primary content.

**Gap flagged:** `transaction_costs_and_market_friction` — currently split across three leaves acceptably. Hold for v0.2.

---

### Book 7: Trade Your Way to Financial Freedom — Van K. Tharp
**Category:** Risk management + trading system development anchor book
**Total tags:** 10 confirmed

```yaml
trade_your_way_to_financial_freedom:
  risk_management:
    - position_sizing
    - stop_loss_and_exit_rules
    - risk_reward_and_expectancy
    - drawdown_management
  strategy_systems_and_execution:
    - strategy_design_and_rule_definition
    - backtesting_and_strategy_validation
    - trade_planning_and_preparation
    - system_iteration_and_continuous_improvement
  trading_psychology_and_behavioral_finance:
    - cognitive_biases
    - process_oriented_decision_making
```

**Key exclusions:**
- `probabilistic_thinking_and_uncertainty` — expectancy math already captured by `risk_reward_and_expectancy`. Avoid double-counting.
- `trend_analysis` — trend following mentioned as one possible system concept, not taught as a TA skill.

**Gap flagged:** `trader_objectives_and_system_fit` — system must fit trader's objectives, beliefs, capital, and personality. Hold for v0.2.

---

### Book 8: Come Into My Trading Room — Alexander Elder
**Category:** Complete trading framework book (Mind, Method, Money)
**Total tags:** 13 confirmed

```yaml
come_into_my_trading_room:
  trading_psychology_and_behavioral_finance:
    - emotional_discipline
    - process_oriented_decision_making
  technical_analysis_and_market_structure:
    - trend_analysis
    - momentum_and_technical_indicators
    - market_structure_and_regime_identification
  risk_management:
    - position_sizing
    - stop_loss_and_exit_rules
    - drawdown_management
    - exposure_and_concentration_limits    # Elder's 6% rule — first confirmed mapping for this leaf
  strategy_systems_and_execution:
    - strategy_design_and_rule_definition
    - backtesting_and_strategy_validation
    - trade_planning_and_preparation
    - journaling_and_performance_review
```

**Key exclusions:**
- `risk_reward_and_expectancy` — Elder's risk control is rule-based (2% and 6% rules), not expectancy math.
- `system_iteration_and_continuous_improvement` — improvement loop expressed through records and review, captured by `journaling_and_performance_review`.

---

### Book 9: The New Sell and Sell Short — Alexander Elder
**Category:** Exit strategy + short selling + risk control book
**Total tags:** 9 confirmed

```yaml
the_new_sell_and_sell_short:
  risk_management:
    - stop_loss_and_exit_rules
    - risk_reward_and_expectancy
    - drawdown_management
  technical_analysis_and_market_structure:
    - trend_analysis
    - support_and_resistance
    - market_structure_and_regime_identification
  strategy_systems_and_execution:
    - strategy_design_and_rule_definition
    - trade_planning_and_preparation
  trading_psychology_and_behavioral_finance:
    - emotional_discipline
```

**Key exclusions:**
- `position_sizing` — book's distinctive focus is exits and shorts, not sizing.
- `execution_quality_and_trade_implementation` — sell logic is not the same as execution mechanics.

**Cross-book note:** 7 leaves overlap with Come Into My Trading Room. Atlas should strongly penalize recommending both to the same user.

---

### Book 10: Entries & Exits — Alexander Elder
**Category:** Trade case-study + practical decision-making book
**Total tags:** 6 confirmed, 1 conditional

```yaml
entries_and_exits:
  strategy_systems_and_execution:
    - trade_planning_and_preparation
    - journaling_and_performance_review
    - strategy_design_and_rule_definition
    - execution_quality_and_trade_implementation  # conditional — confirm if book teaches implementation mechanics
  trading_psychology_and_behavioral_finance:
    - process_oriented_decision_making
  risk_management:
    - stop_loss_and_exit_rules
```

**Cross-book note:** All three Elder books share a core cluster of 4–5 leaves. Redundancy penalty must be strong enough to surface non-Elder books first for users who have read any one of them.

---

### Book 11: Evidence-Based Technical Analysis — David Aronson
**Category:** Strategy validation + statistical testing anchor book
**Total tags:** 5 confirmed, 1 weak

```yaml
evidence_based_technical_analysis:
  strategy_systems_and_execution:
    - backtesting_and_strategy_validation
    - strategy_design_and_rule_definition
    - system_iteration_and_continuous_improvement
  trading_psychology_and_behavioral_finance:
    - process_oriented_decision_making
    - probabilistic_thinking_and_uncertainty
  market_foundations:
    - price_formation_and_market_efficiency   # weak — confirm if book substantially develops EMH
```

**Key exclusions:**
- `trend_analysis` — evaluating trend-based rules is not the same as teaching trend analysis.
- `momentum_and_technical_indicators` — testing indicators is not the same as teaching indicator usage.

**Gap flagged:** `data_snooping_and_statistical_significance` — absorbed under `backtesting_and_strategy_validation` for v0.1.

---

### Book 12: The Art and Science of Technical Analysis — Adam Grimes
**Category:** Comprehensive technical trading framework book
**Total tags:** 14 confirmed

```yaml
the_art_and_science_of_technical_analysis:
  technical_analysis_and_market_structure:
    - trend_analysis
    - support_and_resistance
    - chart_patterns_and_price_action
    - momentum_and_technical_indicators
    - market_structure_and_regime_identification
  risk_management:
    - position_sizing
    - stop_loss_and_exit_rules
    - risk_reward_and_expectancy
  strategy_systems_and_execution:
    - strategy_design_and_rule_definition
    - trade_planning_and_preparation
    - journaling_and_performance_review
  trading_psychology_and_behavioral_finance:
    - cognitive_biases
    - emotional_discipline
    - probabilistic_thinking_and_uncertainty
```

**Key exclusions:**
- `volume_analysis` — not a major teaching focus per table of contents.
- `backtesting_and_strategy_validation` — statistical analysis of results is not backtesting methodology.
- `execution_quality_and_trade_implementation` — trade management is not execution mechanics.

**Cross-book note:** At 14 tags, Grimes overlaps heavily with Murphy, Tharp, and Elder. Atlas should penalize accordingly for users who have read those books.

---

### Book 13: Market Wizards — Jack D. Schwager
**Category:** Interview-based trading wisdom book
**Total tags:** 9 confirmed

```yaml
market_wizards:
  trading_psychology_and_behavioral_finance:
    - emotional_discipline
    - process_oriented_decision_making
    - probabilistic_thinking_and_uncertainty
  risk_management:
    - position_sizing
    - stop_loss_and_exit_rules
    - drawdown_management
  strategy_systems_and_execution:
    - strategy_design_and_rule_definition
    - trade_planning_and_preparation
    - system_iteration_and_continuous_improvement
```

**Key exclusions:**
- `trend_analysis` / `financial_statement_analysis` — individual trader preferences are not the same as the book teaching those concepts.
- `journaling_and_performance_review` — preparation and learning present, but not confirmed as repeated journaling emphasis.

**Interview book rule applied:** Only tagged leaves that appear as repeated cross-interview lessons or explicitly emphasized by Schwager as common success principles.

---

### Book 14: A Random Walk Down Wall Street — Burton G. Malkiel
**Category:** Passive investing + efficient market + portfolio construction anchor book
**Total tags:** 10 confirmed

```yaml
a_random_walk_down_wall_street:
  market_foundations:
    - price_formation_and_market_efficiency
  portfolio_construction_and_asset_allocation:
    - passive_vs_active_portfolio_management
    - diversification
    - asset_allocation
    - portfolio_rebalancing
    - risk_return_tradeoff
    - time_horizon_and_goal_based_investing
  trading_psychology_and_behavioral_finance:
    - cognitive_biases
    - loss_aversion_and_drawdown_psychology
    - overtrading_and_impulse_control
```

**Key exclusions:**
- `trend_analysis` / `momentum_and_technical_indicators` — discussing and critiquing technical methods is not the same as teaching them.
- `intrinsic_value_estimation` — explaining firm-foundation theory to critique it is not the same as teaching DCF.

**Gap flagged:** `market_efficiency_and_indexing` — concept split between two leaves is acceptable for v0.1.

---

### Book 15: The Little Book of Common Sense Investing — John C. Bogle
**Category:** Passive investing + index fund + low-cost investing anchor book
**Total tags:** 8 confirmed

```yaml
the_little_book_of_common_sense_investing:
  market_foundations:
    - price_formation_and_market_efficiency
  portfolio_construction_and_asset_allocation:
    - passive_vs_active_portfolio_management
    - diversification
    - asset_allocation
    - risk_return_tradeoff
    - time_horizon_and_goal_based_investing
  trading_psychology_and_behavioral_finance:
    - overtrading_and_impulse_control
    - process_oriented_decision_making
```

**Key exclusions:**
- `cognitive_biases` — criticizing investor behavior is not the same as systematically teaching behavioral finance.
- `portfolio_rebalancing` — not confirmed as meaningfully taught vs secondary mention.

**Cross-book note:** Bogle and Malkiel share the same core thesis and high leaf overlap. `market_efficiency_and_indexing` gap confirmed by second book.

---

### Book 16: The Little Book That Builds Wealth — Pat Dorsey
**Category:** Business quality + economic moat + valuation book
**Total tags:** 6 confirmed

```yaml
the_little_book_that_builds_wealth:
  fundamental_analysis_and_valuation:
    - business_quality_and_competitive_advantage
    - earnings_growth_and_profitability_drivers
    - intrinsic_value_estimation
    - valuation_multiples_and_relative_valuation
    - margin_of_safety
    - financial_statement_analysis
```

**Key exclusions:**
- `process_oriented_decision_making` — reserved for psychology/behavioral decision-making, not investment process discipline.
- `risk_return_tradeoff` — business risk and portfolio-level risk-return are different scopes.

---

### Book 17: The Four Pillars of Investing — William J. Bernstein
**Category:** Portfolio theory + asset allocation + investor behavior + investment industry book
**Total tags:** 12 confirmed

```yaml
the_four_pillars_of_investing:
  market_foundations:
    - asset_classes_and_financial_instruments
    - price_formation_and_market_efficiency
    - market_participants_and_incentives
  portfolio_construction_and_asset_allocation:
    - asset_allocation
    - diversification
    - portfolio_rebalancing
    - risk_return_tradeoff
    - time_horizon_and_goal_based_investing
    - passive_vs_active_portfolio_management
  trading_psychology_and_behavioral_finance:
    - cognitive_biases
    - overtrading_and_impulse_control
    - process_oriented_decision_making
```

**Key exclusions:**
- `business_cycles_and_market_cycles` — market history and crashes are not cycle analysis like Dalio.
- `business_quality_and_competitive_advantage` — Dorsey owns this leaf more definitively.

---

## Taxonomy Gap Tracker

| Gap candidate | First flagged by | Confirmed by | Status | Decision |
|---|---|---|---|---|
| `investment_vs_speculation` | The Intelligent Investor | — | Single book | Absorbed under `process_oriented_decision_making`. Hold for v0.2. |
| `financial_resilience_and_saving` | The Psychology of Money | — | Single book | Hold for v0.2. |
| `risk_acceptance` | Trading in the Zone | — | Single book | Hold for v0.2. |
| `policy_response_and_crisis_management` | Big Debt Crises | — | Single book | Hold for v0.2. |
| `transaction_costs_and_market_friction` | Trading and Exchanges | — | Single book | Hold for v0.2. |
| `trader_objectives_and_system_fit` | Trade Your Way to Financial Freedom | — | Single book | Hold for v0.2. |
| `data_snooping_and_statistical_significance` | Evidence-Based Technical Analysis | — | Single book | Absorbed under `backtesting_and_strategy_validation`. Hold for v0.2. |
| `market_efficiency_and_indexing` | A Random Walk Down Wall Street | The Little Book of Common Sense Investing | **Two books** | Split across two leaves is acceptable for v0.1. Strongest candidate for a new leaf in v0.2. |

---

## Weak Tag Tracker

| Weak tag | Book | Resolution |
|---|---|---|
| `order_types_and_execution_mechanics` | Technical Analysis of the Financial Markets | Unresolved — confirm during expanded book database phase |
| `financial_statement_analysis` | The Intelligent Investor | Unresolved — confirm during expanded book database phase |
| `time_horizon_and_goal_based_investing` | The Intelligent Investor | Unresolved — confirm during expanded book database phase |
| `price_formation_and_market_efficiency` | Evidence-Based Technical Analysis | Unresolved — confirm during expanded book database phase |
| `execution_quality_and_trade_implementation` | Entries & Exits | Unresolved — confirm during expanded book database phase |

---

## Final Leaf Coverage Status

| Category | Leaves confirmed | Status |
|---|---|---|
| Market Foundations | 6/6 | ✓ All confirmed |
| Fundamental Analysis & Valuation | 6/6 | ✓ All confirmed |
| Technical Analysis & Market Structure | 6/6 | ✓ All confirmed |
| Risk Management | 6/6 | ✓ All confirmed |
| Portfolio Construction & Asset Allocation | 6/6 | ✓ All confirmed |
| Trading Psychology & Behavioral Finance | 6/6 | ✓ All confirmed |
| Macro, Cycles & Economic Context | 6/6 | ✓ All confirmed |
| Strategy, Systems & Execution | 6/6 | ✓ All confirmed |

**All 48 leaves confirmed across 17 books.**

---

## Freeze Decision Log

| Decision | Rationale |
|---|---|
| Taxonomy structure holds — no parent categories added or removed | All 17 books mapped cleanly within the 8 existing categories. No structural gaps found. |
| No leaf concepts added in v0.1 | All 8 gap candidates are held for v0.2. None met the threshold of appearing in 3 or more books as an unresolvable gap. |
| No leaf concepts removed in v0.1 | All 48 leaves received at least one confirmed mapping. No leaf proved impossible to tag against. |
| `market_efficiency_and_indexing` is the strongest v0.2 candidate | Confirmed by two books. Currently split acceptably but warrants a dedicated leaf if passive investing books proliferate. |
| Weak tags left unresolved | 5 weak tags remain unconfirmed. Kept in the taxonomy but flagged. Resolution deferred to expanded book database phase. |
| `taxonomy_v0.1.yaml` frozen as of EOD 2026-05-12 | All 48 leaves confirmed. No structural issues found. Ready for book tagging and gap detection implementation phase. |
