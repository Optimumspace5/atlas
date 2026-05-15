# Atlas Annotation Guidelines

**Version:** v1
**Date:** 2026-05-15
**Status:** Ready for manual annotation v1

## 1. Purpose

Atlas recommends investing and trading books by identifying knowledge gaps in a user's reading history. To do that, Atlas needs manual annotations that describe which taxonomy leaf concepts each book meaningfully teaches.

An annotation is evidence that a book covers a specific concept. It is not a claim that the reader mastered the concept after reading the book. Recommendation logic uses annotations to estimate concept coverage, find under-covered leaves, and rank books by expected coverage gain.

For v1, annotations are manual only. Model-generated annotations may be added later, but manual annotations are the trusted source of ground truth.

## 2. Annotation Model

One annotation connects: `book + taxonomy leaf concept + strength`.

Atlas v1 uses the taxonomy in `data/taxonomy_v0.1.yaml`.

Annotate **leaf concepts only**. Do not annotate parent categories directly.

Allowed: `position_sizing`
Not allowed: `risk_management`

Parent coverage is derived from leaf coverage. For example, a book annotated with `position_sizing`, `drawdown_management`, and `stop_loss_and_exit_rules` contributes coverage to the `risk_management` parent category indirectly.

Strength values are fixed:

| Strength | Label | Meaning |
|---|---|---|
| `1.0` | confirmed | The book meaningfully and repeatedly teaches the concept. |
| `0.5` | weak | The concept is present but secondary, shallow, or not central. |
| `0.3` | conditional | The concept appears plausible from metadata or limited review, but needs verification. |

Do not invent intermediate scores.

## 3. Strength Scale

### `1.0` Confirmed

Use `1.0` when the book clearly teaches the concept as a meaningful part of its content.

A confirmed annotation usually has at least one of these signs:

- The concept is a chapter-level or repeated theme.
- The book explains the concept, not just mentions it.
- A reader would reasonably expect to learn that concept from the book.
- The concept is central to the book's method, framework, or argument.

Examples:

| Book | Leaf | Why `1.0` |
|---|---|---|
| *Trade Your Way to Financial Freedom* | `position_sizing` | Position sizing is central to Tharp's trading system framework and risk control method. |
| *The Little Book That Builds Wealth* | `business_quality_and_competitive_advantage` | The book is explicitly about moats and durable competitive advantage. |

### `0.5` Weak

Use `0.5` when the book covers the concept, but not deeply enough to count as full coverage.

A weak annotation usually has at least one of these signs:

- The concept appears in a limited section.
- The concept supports another main topic.
- The book introduces the idea but does not teach it in depth.
- The concept is implied by the book's framework but not developed thoroughly.

Examples:

| Book | Leaf | Why `0.5` |
|---|---|---|
| *The Intelligent Investor* | `financial_statement_analysis` | Graham uses financial ratios and business data, but the book is not primarily a deep financial statement analysis manual. |
| *Evidence-Based Technical Analysis* | `price_formation_and_market_efficiency` | The book engages with market efficiency as part of evaluating technical analysis, but its main teaching focus is evidence and validation, not price formation itself. |

### `0.3` Conditional

Use `0.3` when the concept is plausible but not confirmed.

Conditional annotations are audit flags. They should not be treated as strong evidence until later review promotes or removes them.

Use `0.3` when:

- Metadata suggests the concept may be covered, but the description is not enough.
- A table of contents or summary hints at the concept without confirming depth.
- The book likely discusses the concept, but the annotator has not reviewed enough evidence.
- The concept is adjacent to the book's main subject and needs manual verification.

Examples:

| Book | Leaf | Why `0.3` |
|---|---|---|
| *Entries & Exits* | `execution_quality_and_trade_implementation` | Case studies may discuss execution, but it needs verification that the book teaches implementation mechanics rather than only trade logic. |
| A broad market history book | `business_cycles_and_market_cycles` | The book may describe market cycles historically, but annotation should remain conditional until it is clear the book teaches cycle analysis. |

## 4. Tagging Rules

### Tag Conservatively

Atlas should prefer missing a marginal tag over adding a false tag.

Over-tagging is more damaging than under-tagging. If every book appears to cover every nearby concept, the gap detector becomes blind and recommendations become similarity-based again.

### Tag What The Book Teaches

Tag a leaf when the book teaches the concept in a way that would help a learner understand or apply it.

Do not tag a leaf merely because:

- The word appears in the title or description.
- The book mentions the concept once.
- The concept is adjacent to the main topic.
- The book critiques a method without teaching it.
- One interview subject or case study mentions it briefly.

### No Parent Fallback

Do not tag a parent category when no specific leaf fits.

If a book is broadly "about risk management" but does not meaningfully teach any risk-management leaf, do not annotate `risk_management`. Either select the specific leaf that is actually taught, or leave risk management untagged.

### Leaf-Level Precision

Choose the most specific leaf that matches the content.

| If the book teaches... | Prefer this leaf |
|---|---|
| How much capital to risk per trade | `position_sizing` |
| Where to exit a losing trade | `stop_loss_and_exit_rules` |
| How losses affect behavior during drawdowns | `loss_aversion_and_drawdown_psychology` |
| How to test a strategy on historical data | `backtesting_and_strategy_validation` |

### Avoid Double Counting

Do not tag two leaves just because they are related.

Examples:

- `probabilistic_thinking_and_uncertainty` is not automatically `risk_reward_and_expectancy`.
- `asset_classes_and_financial_instruments` is not automatically `asset_allocation`.
- `market_structure_and_regime_identification` is not the same as exchange or microstructure design.

## 5. Edge Cases

### Multi-Edition Books

Different editions may appear as separate corpus rows. For annotation purposes, treat editions as equivalent only when the educational content is substantially the same.

If an edition adds major new commentary, chapters, or a different authorial frame, annotate it separately.

Examples:

- A simple reprint with a new ISBN can inherit the same conceptual judgment.
- A revised edition with major commentary should be reviewed directly.

### Introductory vs. Advanced Treatment

Introductory coverage can still be `1.0` if the book clearly teaches the concept at its intended level.

Do not punish a beginner book merely because it is simple. Strength measures evidence of concept coverage, not difficulty.

However, use `0.5` when the book only defines a concept briefly and moves on.

### Survey Books

Survey books can receive multiple annotations, but be careful.

A broad survey may touch many areas without teaching them deeply. Use `1.0` only for concepts that receive meaningful explanation. Use `0.5` for secondary coverage. Avoid assigning 15+ tags unless the book is truly a comprehensive anchor text.

### Memoirs and Interview Books

For memoirs and interview books, tag a concept only when it appears as a repeated lesson or is explicitly emphasized by the author.

One trader mentioning position sizing in one interview is not enough. Multiple interviews emphasizing risk control may justify `position_sizing`, `drawdown_management`, or `stop_loss_and_exit_rules`.

### Books That Critique A Method

A book that critiques a method does not necessarily teach that method.

Example: a passive investing book that criticizes technical analysis should not be tagged with `trend_analysis` or `momentum_and_technical_indicators` unless it actually teaches those techniques.

### Metadata-Only Annotation

When only metadata is available, prefer `0.3` or skip.

Do not assign `1.0` based only on a title unless the title and description are unusually specific and unambiguous.

## 6. Workflow And Quality Bar

A book counts as annotated when the annotator has:

1. Reviewed the title, author, subtitle, description, and available metadata.
2. Considered all 8 parent categories at a scan level.
3. Selected only leaf concepts that are meaningfully supported.
4. Assigned one allowed strength value to every selected leaf.
5. Avoided parent-category tags.
6. Left the book untagged or skipped if it is off-domain or insufficiently supported.

Minimum acceptable annotation per book:

- At least one meaningful leaf annotation for an in-domain book, unless the book is intentionally marked as skipped or off-domain.
- No more than roughly 8-10 leaf annotations for most books.
- More than 10 annotations is allowed only for comprehensive anchor books.
- Every `0.3` conditional annotation should be treated as needing later review.
- If uncertain between `1.0` and `0.5`, choose `0.5`.
- If uncertain between `0.5` and no tag, choose no tag or `0.3` only when follow-up review is intended.

Recommended annotation flow:

1. Read the book metadata.
2. Identify likely parent categories.
3. Inspect only the relevant leaf concepts.
4. Add strong tags first.
5. Add weak tags only if clearly supported.
6. Add conditional tags sparingly.
7. Review the final set for over-tagging.

## 7. Open Questions

- **[v1.x]** Should `annotations_v1.csv` include a free-text rationale column for every annotation?
- **[v1.x]** Should skipped books be tracked in a separate file with skip reasons?
- **[v1.x]** Should conditional annotations require later promotion/removal before recommendation evaluation?
- **[v2]** Should model-generated annotations be stored separately from manual annotations for comparison?
- **[v2]** Should parent coverage thresholds be derived from summed leaf strengths or binary leaf presence?
- **[depends]** Should multi-edition books share annotations once an edition-canonicalization layer exists?
