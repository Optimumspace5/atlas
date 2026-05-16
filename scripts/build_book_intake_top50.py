"""Build a reconciled intake queue for the first 50 sourced books.

This script preserves the sourced top-50 list as structured input, matches each
candidate against `corpus_merged_v1.csv`, checks existing annotations, and writes
`book_intake_top50_v1.csv` as the next human annotation queue.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CORPUS_CSV = Path("data/corpus_merged_v1.csv")
ANNOTATIONS_CSV = Path("data/annotations_v1.csv")
OUTPUT_CSV = Path("data/book_intake_top50_v1.csv")


TOP_50: list[dict[str, Any]] = [
    {
        "rank": 1,
        "title": "The Intelligent Investor",
        "author": "Benjamin Graham",
        "publication_year": "1949",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "intrinsic_value_estimation; margin_of_safety; valuation_multiples_and_relative_valuation; emotional_discipline",
        "rating_source": "Goodreads",
        "average_rating": "4.23",
        "rating_count": "142699",
        "reputation_evidence": "Widely cited value investing classic; high reader validation",
        "why_include": "Core value-investing anchor for margin of safety and investor temperament",
        "source_urls": "https://www.goodreads.com/book/show/106835.The_Intelligent_Investor; https://en.wikipedia.org/wiki/The_Intelligent_Investor",
    },
    {
        "rank": 2,
        "title": "Security Analysis",
        "author": "Benjamin Graham and David Dodd",
        "publication_year": "1934",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "financial_statement_analysis; intrinsic_value_estimation; margin_of_safety; valuation_multiples_and_relative_valuation",
        "rating_source": "Goodreads",
        "average_rating": "4.30",
        "rating_count": "9876",
        "reputation_evidence": "Canonical professional value investing text",
        "why_include": "Needed for deeper valuation and security-analysis coverage beyond beginner books",
        "source_urls": "https://www.goodreads.com/book/show/203409.Security_Analysis",
    },
    {
        "rank": 3,
        "title": "Margin of Safety",
        "author": "Seth A. Klarman",
        "publication_year": "1991",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "margin_of_safety; intrinsic_value_estimation; risk_reward_and_expectancy; loss_aversion_and_drawdown_psychology",
        "rating_source": "Goodreads",
        "average_rating": "4.33",
        "rating_count": "7133",
        "reputation_evidence": "Cult value-investing classic; strong specialist reputation",
        "why_include": "Best single book for risk-averse value investing and downside-first thinking",
        "source_urls": "https://www.goodreads.com/book/show/746936; https://www.goodreads.com/author/show/395523.Seth_A_Klarman",
    },
    {
        "rank": 4,
        "title": "The Essays of Warren Buffett",
        "author": "Warren Buffett and Lawrence Cunningham",
        "publication_year": "1997",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "business_quality_and_competitive_advantage; earnings_growth_and_profitability_drivers; passive_vs_active_portfolio_management; process_oriented_decision_making",
        "rating_source": "Goodreads",
        "average_rating": "4.33",
        "rating_count": "8293",
        "reputation_evidence": "High Goodreads validation; primary-source Buffett shareholder-letter synthesis",
        "why_include": "Covers business quality, capital allocation, and long-term ownership logic",
        "source_urls": "https://www.goodreads.com/en/book/show/145565.The_Essays_of_Warren_Buffett_",
    },
    {
        "rank": 5,
        "title": "Common Stocks and Uncommon Profits",
        "author": "Philip A. Fisher",
        "publication_year": "1958",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "business_quality_and_competitive_advantage; earnings_growth_and_profitability_drivers; intrinsic_value_estimation",
        "rating_source": "Goodreads",
        "average_rating": "4.14",
        "rating_count": "16635",
        "reputation_evidence": "Classic growth-quality investing book with large rating count",
        "why_include": "Adds qualitative business-analysis coverage that Graham-style books underweight",
        "source_urls": "https://www.goodreads.com/en/book/show/25574.Common_Stocks_and_Uncommon_Profits_and_Other_Writings",
    },
    {
        "rank": 6,
        "title": "One Up On Wall Street",
        "author": "Peter Lynch",
        "publication_year": "1989",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "business_quality_and_competitive_advantage; earnings_growth_and_profitability_drivers; valuation_multiples_and_relative_valuation; market_participants_and_incentives",
        "rating_source": "Goodreads",
        "average_rating": "4.29",
        "rating_count": "41314",
        "reputation_evidence": "High rating count; mainstream investing classic",
        "why_include": "Useful for practical company classification, stock narratives, and retail-investor edge claims",
        "source_urls": "https://www.goodreads.com/book/show/762462.One_Up_On_Wall_Street",
    },
    {
        "rank": 7,
        "title": "The Most Important Thing",
        "author": "Howard Marks",
        "publication_year": "2011",
        "primary_parent_category": "risk_management",
        "likely_leaf_nodes": "risk_reward_and_expectancy; drawdown_management; probabilistic_thinking_and_uncertainty; process_oriented_decision_making",
        "rating_source": "Goodreads",
        "average_rating": "4.32",
        "rating_count": "16745",
        "reputation_evidence": "Highly rated Oaktree memo-based investing text",
        "why_include": "Strong bridge between valuation, risk, second-level thinking, and cycles",
        "source_urls": "https://www.goodreads.com/book/show/10454418-the-most-important-thing",
    },
    {
        "rank": 8,
        "title": "You Can Be a Stock Market Genius",
        "author": "Joel Greenblatt",
        "publication_year": "1997",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "intrinsic_value_estimation; market_participants_and_incentives; valuation_multiples_and_relative_valuation; margin_of_safety",
        "rating_source": "Goodreads",
        "average_rating": "4.21",
        "rating_count": "8702",
        "reputation_evidence": "High specialist reputation; strong Goodreads rating",
        "why_include": "Covers special situations, spin-offs, restructurings, and inefficient-market niches",
        "source_urls": "https://www.goodreads.com/work/editions/111891-you-can-be-a-stock-market-genius-uncover-the-secret-hiding-places-of-st",
    },
    {
        "rank": 9,
        "title": "The Little Book That Beats the Market",
        "author": "Joel Greenblatt",
        "publication_year": "2005",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "valuation_multiples_and_relative_valuation; earnings_growth_and_profitability_drivers; strategy_design_and_rule_definition",
        "rating_source": "Goodreads",
        "average_rating": "not_verified",
        "rating_count": "9771",
        "reputation_evidence": "Popular systematic value-quality framework",
        "why_include": "Useful for simple rule-based factor thinking and quality/value screening",
        "source_urls": "https://www.goodreads.com/book/show/75889.The_Little_Book_That_Beats_the_Market; https://www.goodreads.com/en/book/show/8247775-the-little-book-that-still-beats-the-market",
    },
    {
        "rank": 10,
        "title": "Poor Charlie's Almanack",
        "author": "Charles T. Munger",
        "publication_year": "2005",
        "primary_parent_category": "trading_psychology_and_behavioral_finance",
        "likely_leaf_nodes": "cognitive_biases; process_oriented_decision_making; probabilistic_thinking_and_uncertainty; business_quality_and_competitive_advantage",
        "rating_source": "Goodreads",
        "average_rating": "4.39",
        "rating_count": "19224",
        "reputation_evidence": "Strong reader validation; Munger mental-model canon",
        "why_include": "Excellent for cognitive-bias tagging and multidisciplinary decision-making",
        "source_urls": "https://www.goodreads.com/book/show/944652.Poor_Charlie_s_Almanack; https://www.goodreads.com/author/list/236437.Charles_T_Munger",
    },
    {
        "rank": 11,
        "title": "The Outsiders",
        "author": "William N. Thorndike Jr.",
        "publication_year": "2012",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "business_quality_and_competitive_advantage; earnings_growth_and_profitability_drivers; intrinsic_value_estimation",
        "rating_source": "Goodreads",
        "average_rating": "4.23",
        "rating_count": "13841",
        "reputation_evidence": "High rating count; capital-allocation classic",
        "why_include": "Adds CEO capital allocation and business-quality evidence to the taxonomy",
        "source_urls": "https://www.goodreads.com/book/show/13586932-the-outsiders",
    },
    {
        "rank": 12,
        "title": "Value Investing: From Graham to Buffett and Beyond",
        "author": "Bruce Greenwald et al.",
        "publication_year": "2001",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "intrinsic_value_estimation; margin_of_safety; financial_statement_analysis; valuation_multiples_and_relative_valuation",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Columbia value-investing lineage; academic/practitioner text",
        "why_include": "Useful as a structured bridge from Graham principles to modern valuation application",
        "source_urls": "https://books.google.com/books/about/Value_Investing.html?id=Jg6rAAAACAAJ",
    },
    {
        "rank": 13,
        "title": "International Financial Statement Analysis",
        "author": "Thomas R. Robinson et al.",
        "publication_year": "2008",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "financial_statement_analysis; earnings_growth_and_profitability_drivers; valuation_multiples_and_relative_valuation",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "CFA Institute Investment Series; Wiley publication",
        "why_include": "Best fit for rigorous accounting and statement-analysis annotation",
        "source_urls": "https://www.wiley.com/en-us/International%2BFinancial%2BStatement%2BAnalysis%2C%2B4th%2BEdition-p-9781119628149",
    },
    {
        "rank": 14,
        "title": "Equity Asset Valuation",
        "author": "Jerald E. Pinto et al.",
        "publication_year": "2007",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "intrinsic_value_estimation; valuation_multiples_and_relative_valuation; earnings_growth_and_profitability_drivers",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "CFA Institute Investment Series; Wiley publication",
        "why_include": "Directly covers valuation models and equity-pricing methods",
        "source_urls": "https://www.wiley.com/en-ie/Equity%2BAsset%2BValuation%2C%2B4th%2BEdition-p-9781119628101",
    },
    {
        "rank": 15,
        "title": "Valuation: Measuring and Managing the Value of Companies",
        "author": "McKinsey & Company / Koller Goedhart Wessels",
        "publication_year": "1990",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "intrinsic_value_estimation; financial_statement_analysis; earnings_growth_and_profitability_drivers; valuation_multiples_and_relative_valuation",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Practitioner-standard corporate valuation text",
        "why_include": "Needed for DCF, ROIC, growth, and enterprise-valuation coverage",
        "source_urls": "https://www.wiley.com/en-us/Valuation%3A%2BMeasuring%2Band%2BManaging%2Bthe%2BValue%2Bof%2BCompanies%2C%2B7th%2BEdition-p-9781119611868",
    },
    {
        "rank": 16,
        "title": "Investment Valuation",
        "author": "Aswath Damodaran",
        "publication_year": "1994",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "intrinsic_value_estimation; valuation_multiples_and_relative_valuation; financial_statement_analysis",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Damodaran valuation canon; university/publisher reputation",
        "why_include": "High-value annotation source for DCF, relative valuation, and valuation assumptions",
        "source_urls": "https://pages.stern.nyu.edu/~adamodar/",
    },
    {
        "rank": 17,
        "title": "Quality Investing",
        "author": "Lawrence A. Cunningham Torkell T. Eide and Patrick Hargreaves",
        "publication_year": "2016",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "business_quality_and_competitive_advantage; earnings_growth_and_profitability_drivers; intrinsic_value_estimation",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Specialist quality-investing text",
        "why_include": "Fills quality/moat/profitability leaves better than broad investing books",
        "source_urls": "https://www.harriman-house.com/qualityinvesting",
    },
    {
        "rank": 18,
        "title": "Financial Shenanigans",
        "author": "Howard Schilit",
        "publication_year": "1993",
        "primary_parent_category": "fundamental_analysis_and_valuation",
        "likely_leaf_nodes": "financial_statement_analysis; earnings_growth_and_profitability_drivers; risk_reward_and_expectancy",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Well-known forensic accounting text",
        "why_include": "Important for accounting red flags and avoiding false valuation signals",
        "source_urls": "https://www.mheducation.com/highered/product/financial-shenanigans-fourth-edition-schilit-perler/M9781260117264.html",
    },
    {
        "rank": 19,
        "title": "A Random Walk Down Wall Street",
        "author": "Burton G. Malkiel",
        "publication_year": "1973",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "price_formation_and_market_efficiency; passive_vs_active_portfolio_management; diversification; risk_return_tradeoff",
        "rating_source": "Goodreads",
        "average_rating": "4.14",
        "rating_count": "41431",
        "reputation_evidence": "Large rating count; efficient-market/passive-investing classic",
        "why_include": "Core text for market efficiency and passive-versus-active reasoning",
        "source_urls": "https://www.goodreads.com/book/show/900892.A_Random_Walk_Down_Wall_Street",
    },
    {
        "rank": 20,
        "title": "The Little Book of Common Sense Investing",
        "author": "John C. Bogle",
        "publication_year": "2007",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "passive_vs_active_portfolio_management; diversification; portfolio_rebalancing; time_horizon_and_goal_based_investing",
        "rating_source": "Goodreads",
        "average_rating": "4.46",
        "rating_count": "2584",
        "reputation_evidence": "Bogleheads-aligned classic; strong rating",
        "why_include": "Needed for index investing, low-cost funds, and passive portfolio logic",
        "source_urls": "https://www.goodreads.com/work/editions/165246-the-little-book-of-common-sense-investing-the-only-way-to-guarantee-you; https://www.bogleheads.org/wiki/Taylor_Larimore%27s_Investment_Gems",
    },
    {
        "rank": 21,
        "title": "Common Sense on Mutual Funds",
        "author": "John C. Bogle",
        "publication_year": "1999",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "passive_vs_active_portfolio_management; diversification; portfolio_rebalancing; risk_return_tradeoff",
        "rating_source": "Goodreads",
        "average_rating": "not_verified",
        "rating_count": "2410",
        "reputation_evidence": "Bogle mutual-fund classic; Bogleheads relevance",
        "why_include": "Covers mutual funds, fees, active-management limitations, and indexing",
        "source_urls": "https://www.goodreads.com/book/show/7081902-common-sense-on-mutual-funds; https://www.bogleheads.org/wiki/John_Bogle_biographical_information",
    },
    {
        "rank": 22,
        "title": "The Four Pillars of Investing",
        "author": "William J. Bernstein",
        "publication_year": "2002",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "asset_allocation; diversification; risk_return_tradeoff; cognitive_biases; time_horizon_and_goal_based_investing",
        "rating_source": "Goodreads",
        "average_rating": "4.24",
        "rating_count": "6381",
        "reputation_evidence": "High reader validation; Bogleheads-adjacent recommendation",
        "why_include": "Strong all-around portfolio framework combining theory, history, psychology, and allocation",
        "source_urls": "https://www.goodreads.com/en/book/show/79351.The_Four_Pillars_of_Investing",
    },
    {
        "rank": 23,
        "title": "All About Asset Allocation",
        "author": "Richard A. Ferri",
        "publication_year": "2006",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "asset_allocation; diversification; portfolio_rebalancing; risk_return_tradeoff",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Bogleheads-style asset-allocation text by Rick Ferri",
        "why_include": "Useful focused source for allocation and rebalancing leaves",
        "source_urls": "https://www.mheducation.com/highered/product/all-about-asset-allocation-second-edition-ferri/M9780071700788.html",
    },
    {
        "rank": 24,
        "title": "Asset Allocation: Balancing Financial Risk",
        "author": "Roger C. Gibson",
        "publication_year": "1989",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "asset_allocation; diversification; risk_return_tradeoff; portfolio_rebalancing",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Longstanding asset-allocation reference",
        "why_include": "Good for asset-class diversification and risk-balancing concepts",
        "source_urls": "https://www.mheducation.com/highered/product/asset-allocation-balancing-financial-risk-fifth-edition-gibson/M9780071804189.html",
    },
    {
        "rank": 25,
        "title": "Active Portfolio Management",
        "author": "Richard Grinold and Ronald Kahn",
        "publication_year": "1999",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "risk_return_tradeoff; passive_vs_active_portfolio_management; exposure_and_concentration_limits; strategy_design_and_rule_definition",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Institutional active-management classic",
        "why_include": "Important for alpha, benchmark risk, information ratio, and portfolio constraints",
        "source_urls": "https://www.mheducation.com/highered/product/active-portfolio-management-grinold-kahn/M9780070248823.html",
    },
    {
        "rank": 26,
        "title": "Portfolio Management in Practice, CFA Institute",
        "author": "CFA Institute",
        "publication_year": "2020",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "asset_allocation; portfolio_rebalancing; risk_return_tradeoff; time_horizon_and_goal_based_investing",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "CFA Institute Investment Series; Wiley publication",
        "why_include": "Institutional portfolio-process anchor for structured annotation",
        "source_urls": "https://www.wiley.com/en-gb/Portfolio%2BManagement%2Bin%2BPractice%2C%2BVolume%2B1%3A%2BInvestment%2BManagement-p-9781119743729",
    },
    {
        "rank": 27,
        "title": "Fixed Income Analysis",
        "author": "Frank J. Fabozzi / CFA Institute",
        "publication_year": "2000",
        "primary_parent_category": "market_foundations",
        "likely_leaf_nodes": "asset_classes_and_financial_instruments; interest_rates_and_monetary_policy; risk_return_tradeoff; inflation_and_purchasing_power",
        "rating_source": "Goodreads",
        "average_rating": "4.40",
        "rating_count": "5",
        "reputation_evidence": "CFA Institute Investment Series; specialist text",
        "why_include": "Needed because Atlas should not become equities-only; covers bonds and rates",
        "source_urls": "https://www.wiley.com/en-be/Fixed%2BIncome%2BAnalysis%2C%2B5th%2BEdition-p-9781119850540; https://books.google.com/books/about/Fixed_Income_Analysis.html?id=ljEaBgAAQBAJ",
    },
    {
        "rank": 28,
        "title": "The Bogleheads' Guide to Investing",
        "author": "Taylor Larimore Mel Lindauer and Michael LeBoeuf",
        "publication_year": "2006",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "asset_allocation; diversification; portfolio_rebalancing; passive_vs_active_portfolio_management; time_horizon_and_goal_based_investing",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Bogleheads community reference; popular first-book recommendation",
        "why_include": "Good broad passive-investing and implementation bridge",
        "source_urls": "https://www.bogleheads.org/wiki/Book_recommendations_and_reviews; https://www.amazon.com/Bogleheads-Guide-Investing-Taylor-Larimore/dp/0470067365",
    },
    {
        "rank": 29,
        "title": "Stocks for the Long Run",
        "author": "Jeremy J. Siegel",
        "publication_year": "1994",
        "primary_parent_category": "portfolio_construction_and_asset_allocation",
        "likely_leaf_nodes": "risk_return_tradeoff; asset_allocation; inflation_and_purchasing_power; business_cycles_and_market_cycles",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Longstanding historical equity-return reference",
        "why_include": "Covers long-run asset returns and stock/bond allocation context",
        "source_urls": "https://www.mheducation.com/highered/product/stocks-long-run-siegel/M9781264269804.html",
    },
    {
        "rank": 30,
        "title": "Manias Panics and Crashes",
        "author": "Charles P. Kindleberger and Robert Aliber",
        "publication_year": "1978",
        "primary_parent_category": "macro_cycles_and_economic_context",
        "likely_leaf_nodes": "credit_debt_and_financial_crises; business_cycles_and_market_cycles; market_participants_and_incentives; loss_aversion_and_drawdown_psychology",
        "rating_source": "Goodreads",
        "average_rating": "3.94",
        "rating_count": "4153",
        "reputation_evidence": "Classic financial-crisis history text",
        "why_include": "Essential for bubbles, crashes, credit cycles, and crisis-pattern annotation",
        "source_urls": "https://www.goodreads.com/en/book/show/367596.Manias_Panics_and_Crashes",
    },
    {
        "rank": 31,
        "title": "This Time Is Different",
        "author": "Carmen Reinhart and Kenneth Rogoff",
        "publication_year": "2009",
        "primary_parent_category": "macro_cycles_and_economic_context",
        "likely_leaf_nodes": "credit_debt_and_financial_crises; currency_and_global_macro_forces; economic_indicators_and_data_interpretation",
        "rating_source": "Goodreads",
        "average_rating": "3.76",
        "rating_count": "6683",
        "reputation_evidence": "Major empirical financial-crisis text",
        "why_include": "Adds sovereign debt, banking crises, currency crises, and long historical data",
        "source_urls": "https://www.goodreads.com/book/show/6372440-this-time-is-different",
    },
    {
        "rank": 32,
        "title": "The Alchemy of Finance",
        "author": "George Soros",
        "publication_year": "1987",
        "primary_parent_category": "macro_cycles_and_economic_context",
        "likely_leaf_nodes": "business_cycles_and_market_cycles; currency_and_global_macro_forces; probabilistic_thinking_and_uncertainty; market_structure_and_regime_identification",
        "rating_source": "Goodreads",
        "average_rating": "3.75",
        "rating_count": "4690",
        "reputation_evidence": "Macro investing classic by Soros",
        "why_include": "Useful for reflexivity, macro regime shifts, and market feedback loops",
        "source_urls": "https://www.goodreads.com/book/show/369708.The_Alchemy_of_Finance",
    },
    {
        "rank": 33,
        "title": "Principles for Dealing with the Changing World Order",
        "author": "Ray Dalio",
        "publication_year": "2021",
        "primary_parent_category": "macro_cycles_and_economic_context",
        "likely_leaf_nodes": "business_cycles_and_market_cycles; credit_debt_and_financial_crises; currency_and_global_macro_forces; inflation_and_purchasing_power",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Popular macro-cycle framework by Bridgewater founder",
        "why_include": "Useful for long-cycle, debt-cycle, reserve-currency, and geopolitical macro leaves",
        "source_urls": "https://www.simonandschuster.com/books/Principles-for-Dealing-with-the-Changing-World-Order/Ray-Dalio/9781982160272",
    },
    {
        "rank": 34,
        "title": "Lords of Finance",
        "author": "Liaquat Ahamed",
        "publication_year": "2009",
        "primary_parent_category": "macro_cycles_and_economic_context",
        "likely_leaf_nodes": "interest_rates_and_monetary_policy; credit_debt_and_financial_crises; currency_and_global_macro_forces",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Pulitzer-winning financial history",
        "why_include": "Adds central banking, gold standard, monetary policy, and crisis history",
        "source_urls": "https://www.penguinrandomhouse.com/books/297323/lords-of-finance-by-liaquat-ahamed/",
    },
    {
        "rank": 35,
        "title": "Thinking Fast and Slow",
        "author": "Daniel Kahneman",
        "publication_year": "2011",
        "primary_parent_category": "trading_psychology_and_behavioral_finance",
        "likely_leaf_nodes": "cognitive_biases; loss_aversion_and_drawdown_psychology; process_oriented_decision_making; probabilistic_thinking_and_uncertainty",
        "rating_source": "Goodreads",
        "average_rating": "4.17",
        "rating_count": "602324",
        "reputation_evidence": "Massive reader validation; Nobel-linked behavioral decision science",
        "why_include": "Core behavioral-finance foundation, even though not market-specific",
        "source_urls": "https://www.goodreads.com/book/show/11468377-thinking-fast-and-slow",
    },
    {
        "rank": 36,
        "title": "The Psychology of Money",
        "author": "Morgan Housel",
        "publication_year": "2020",
        "primary_parent_category": "trading_psychology_and_behavioral_finance",
        "likely_leaf_nodes": "emotional_discipline; loss_aversion_and_drawdown_psychology; time_horizon_and_goal_based_investing; process_oriented_decision_making",
        "rating_source": "Goodreads",
        "average_rating": "4.28",
        "rating_count": "342441",
        "reputation_evidence": "Very high reader validation",
        "why_include": "Good accessible bridge for investor behavior, time horizon, and temperament",
        "source_urls": "https://www.goodreads.com/book/show/41881472-the-psychology-of-money",
    },
    {
        "rank": 37,
        "title": "Fooled by Randomness",
        "author": "Nassim Nicholas Taleb",
        "publication_year": "2001",
        "primary_parent_category": "trading_psychology_and_behavioral_finance",
        "likely_leaf_nodes": "probabilistic_thinking_and_uncertainty; cognitive_biases; risk_reward_and_expectancy; process_oriented_decision_making",
        "rating_source": "Goodreads",
        "average_rating": "4.08",
        "rating_count": "72337",
        "reputation_evidence": "High rating count; randomness and uncertainty classic",
        "why_include": "Strong for survivorship bias, luck, probability, and trader overconfidence",
        "source_urls": "https://www.goodreads.com/book/show/38315.Fooled_by_Randomness",
    },
    {
        "rank": 38,
        "title": "Against the Gods: The Remarkable Story of Risk",
        "author": "Peter L. Bernstein",
        "publication_year": "1996",
        "primary_parent_category": "risk_management",
        "likely_leaf_nodes": "probabilistic_thinking_and_uncertainty; risk_return_tradeoff; risk_reward_and_expectancy; cognitive_biases",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Risk-history classic",
        "why_include": "Excellent conceptual foundation for risk, probability, and uncertainty",
        "source_urls": "https://www.penguinrandomhouse.com/books/12459/against-the-gods-by-peter-l-bernstein/",
    },
    {
        "rank": 39,
        "title": "Market Wizards",
        "author": "Jack D. Schwager",
        "publication_year": "1989",
        "primary_parent_category": "trading_psychology_and_behavioral_finance",
        "likely_leaf_nodes": "emotional_discipline; process_oriented_decision_making; risk_reward_and_expectancy; drawdown_management; trade_planning_and_preparation",
        "rating_source": "Goodreads",
        "average_rating": "4.28",
        "rating_count": "10681",
        "reputation_evidence": "High rating count; trader-interview classic",
        "why_include": "Useful for process, risk discipline, and varied trading styles without overfitting to one method",
        "source_urls": "https://www.goodreads.com/book/show/30168787-market-wizards",
    },
    {
        "rank": 40,
        "title": "Trading in the Zone",
        "author": "Mark Douglas",
        "publication_year": "2000",
        "primary_parent_category": "trading_psychology_and_behavioral_finance",
        "likely_leaf_nodes": "emotional_discipline; overtrading_and_impulse_control; loss_aversion_and_drawdown_psychology; probabilistic_thinking_and_uncertainty",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Widely recommended trading-psychology book",
        "why_include": "Covers emotional discipline and probabilistic execution mindset",
        "source_urls": "https://www.penguinrandomhouse.com/books/288984/trading-in-the-zone-by-mark-douglas/",
    },
    {
        "rank": 41,
        "title": "Technical Analysis of the Financial Markets",
        "author": "John J. Murphy",
        "publication_year": "1999",
        "primary_parent_category": "technical_analysis_and_market_structure",
        "likely_leaf_nodes": "trend_analysis; support_and_resistance; chart_patterns_and_price_action; momentum_and_technical_indicators; volume_analysis",
        "rating_source": "Goodreads",
        "average_rating": "4.21",
        "rating_count": "4089",
        "reputation_evidence": "Technical-analysis reference; high specialist reputation",
        "why_include": "Core introductory technical-analysis coverage across many leaves",
        "source_urls": "https://www.goodreads.com/book/show/212102; https://books.google.com/books/about/Technical_Analysis_of_the_Financial_Mark.html?id=teitAAAAQBAJ",
    },
    {
        "rank": 42,
        "title": "Technical Analysis of Stock Trends",
        "author": "Robert D. Edwards and John Magee",
        "publication_year": "1948",
        "primary_parent_category": "technical_analysis_and_market_structure",
        "likely_leaf_nodes": "trend_analysis; support_and_resistance; chart_patterns_and_price_action; market_structure_and_regime_identification",
        "rating_source": "Goodreads",
        "average_rating": "4.16",
        "rating_count": "747",
        "reputation_evidence": "Classic chart-pattern text; IFTA reading-list relevance",
        "why_include": "Historical anchor for chart patterns, trend structure, and support/resistance",
        "source_urls": "https://www.goodreads.com/book/show/474623.Technical_Analysis_of_Stock_Trends; https://www.oreilly.com/library/view/a-handbook-of/9781118498910/38_appendixb.html",
    },
    {
        "rank": 43,
        "title": "Japanese Candlestick Charting Techniques",
        "author": "Steve Nison",
        "publication_year": "1991",
        "primary_parent_category": "technical_analysis_and_market_structure",
        "likely_leaf_nodes": "chart_patterns_and_price_action; support_and_resistance; trend_analysis",
        "rating_source": "Goodreads",
        "average_rating": "4.31",
        "rating_count": "2055",
        "reputation_evidence": "High specialist rating; candlestick-analysis standard",
        "why_include": "Needed for candlestick-specific price-action tagging",
        "source_urls": "https://www.goodreads.com/book/show/20931958-japanese-candlestick-charting-techniques",
    },
    {
        "rank": 44,
        "title": "Technical Analysis: The Complete Resource for Financial Market Technicians",
        "author": "Charles D. Kirkpatrick II and Julie R. Dahlquist",
        "publication_year": "2006",
        "primary_parent_category": "technical_analysis_and_market_structure",
        "likely_leaf_nodes": "trend_analysis; momentum_and_technical_indicators; chart_patterns_and_price_action; market_structure_and_regime_identification; backtesting_and_strategy_validation",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Official companion to CMT program per publisher/O'Reilly description",
        "why_include": "More rigorous, evidence-aware TA reference than many retail TA books",
        "source_urls": "https://www.oreilly.com/library/view/technical-analysis-the/9780132599580/; https://www.pearson.com/en-au/subject-catalog/p/technical-analysis-the-complete-resource-for-financial-market-technicians/P200000000373/9780134137162",
    },
    {
        "rank": 45,
        "title": "Evidence-Based Technical Analysis",
        "author": "David Aronson",
        "publication_year": "2006",
        "primary_parent_category": "technical_analysis_and_market_structure",
        "likely_leaf_nodes": "backtesting_and_strategy_validation; momentum_and_technical_indicators; strategy_design_and_rule_definition; probabilistic_thinking_and_uncertainty",
        "rating_source": "Goodreads",
        "average_rating": "3.66",
        "rating_count": "154",
        "reputation_evidence": "Specialist evidence-based TA text",
        "why_include": "Important because it teaches how to test signals instead of just naming indicators",
        "source_urls": "https://www.goodreads.com/en/book/show/203967.Evidence_Based_Technical_Analysis; https://www.cxoadvisory.com/technical-trading/evidence-based-technical-analysis-applying-the-scientific-method-and-statistical-inference-to-trading-signals-chapter-by-chapter-review/",
    },
    {
        "rank": 46,
        "title": "Trading and Exchanges: Market Microstructure for Practitioners",
        "author": "Larry Harris",
        "publication_year": "2002",
        "primary_parent_category": "market_foundations",
        "likely_leaf_nodes": "exchanges_brokers_and_market_infrastructure; order_types_and_execution_mechanics; liquidity_and_market_depth; price_formation_and_market_efficiency; market_participants_and_incentives",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Oxford University Press; market microstructure classic",
        "why_include": "Critical for market mechanics, order books, liquidity, spreads, and execution logic",
        "source_urls": "https://global.oup.com/academic/product/trading-and-exchanges-9780195144703; https://books.google.com/books/about/Trading_and_Exchanges.html?id=t-TQCwAAQBAJ",
    },
    {
        "rank": 47,
        "title": "Market Microstructure in Practice",
        "author": "Charles-Albert Lehalle and Sophie Laruelle",
        "publication_year": "2013",
        "primary_parent_category": "market_foundations",
        "likely_leaf_nodes": "liquidity_and_market_depth; exchanges_brokers_and_market_infrastructure; order_types_and_execution_mechanics; price_formation_and_market_efficiency",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "World Scientific specialist book; institutional microstructure focus",
        "why_include": "Adds modern electronic-market and liquidity structure coverage",
        "source_urls": "https://www.worldscientific.com/worldscibooks/10.1142/10739",
    },
    {
        "rank": 48,
        "title": "Algorithmic Trading and DMA",
        "author": "Barry Johnson",
        "publication_year": "2010",
        "primary_parent_category": "strategy_systems_and_execution",
        "likely_leaf_nodes": "execution_quality_and_trade_implementation; order_types_and_execution_mechanics; liquidity_and_market_depth; trade_planning_and_preparation",
        "rating_source": "expert sources only",
        "average_rating": "N/A",
        "rating_count": "N/A",
        "reputation_evidence": "Institutional execution/DMA reference; QuantInsti/Quantocracy relevance",
        "why_include": "Best fit for direct market access, execution, and institutional order handling",
        "source_urls": "https://www.amazon.sg/Algorithmic-Trading-DMA-Introduction-Strategies/dp/0956399207; https://quantocracy.com/books/",
    },
    {
        "rank": 49,
        "title": "Quantitative Trading",
        "author": "Ernest P. Chan",
        "publication_year": "2008",
        "primary_parent_category": "strategy_systems_and_execution",
        "likely_leaf_nodes": "strategy_design_and_rule_definition; backtesting_and_strategy_validation; system_iteration_and_continuous_improvement; risk_reward_and_expectancy",
        "rating_source": "Goodreads",
        "average_rating": "3.76",
        "rating_count": "774",
        "reputation_evidence": "QuantStart-recommended beginner quant-trading text",
        "why_include": "Good practical bridge into systematic strategy design and testing",
        "source_urls": "https://www.goodreads.com/book/show/4977694-quantitative-trading; https://www.quantstart.com/articles/Top-5-Essential-Beginner-Books-for-Algorithmic-Trading/",
    },
    {
        "rank": 50,
        "title": "Systematic Trading",
        "author": "Robert Carver",
        "publication_year": "2015",
        "primary_parent_category": "strategy_systems_and_execution",
        "likely_leaf_nodes": "strategy_design_and_rule_definition; risk_reward_and_expectancy; position_sizing; system_iteration_and_continuous_improvement; drawdown_management",
        "rating_source": "Goodreads",
        "average_rating": "4.02",
        "rating_count": "266",
        "reputation_evidence": "High specialist rating; systematic-trading practitioner text",
        "why_include": "Strong for rule-based process, risk targeting, and systematic portfolio construction",
        "source_urls": "https://www.goodreads.com/book/show/25900953-systematic-trading; https://www.goodreads.com/shelf/show/systematic-trading",
    },
]


OUTPUT_COLUMNS: list[str] = [
    "rank",
    "title",
    "author",
    "publication_year",
    "primary_parent_category",
    "likely_leaf_nodes",
    "rating_source",
    "average_rating",
    "rating_count",
    "reputation_evidence",
    "why_include",
    "source_urls",
    "status",
    "recommended_action",
    "corpus_row",
    "corpus_title",
    "corpus_author",
    "match_score",
    "match_author_ok",
    "corpus_source",
    "canonical_isbn_13",
    "already_annotated",
    "annotation_count",
    "notes",
]


def _clean_cell(value: Any) -> str:
    """Return a stripped string for CSV-like input."""
    return "" if value is None else str(value).strip()


def _normalize_text(value: Any) -> str:
    """Normalize text for fuzzy matching."""
    text = _clean_cell(value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _title_variants(title: Any, subtitle: Any = "") -> list[str]:
    """Return title variants used for fuzzy matching."""
    raw_title = _clean_cell(title)
    raw_subtitle = _clean_cell(subtitle)
    variants = [raw_title]
    if ":" in raw_title:
        variants.append(raw_title.split(":", 1)[0])
    if raw_subtitle:
        variants.append(f"{raw_title} {raw_subtitle}")
    return [item for item in variants if _normalize_text(item)]


def _title_similarity(source_title: str, corpus_row: dict[str, Any]) -> float:
    """Return best title similarity between a sourced book and a corpus row."""
    source_variants = _title_variants(source_title)
    corpus_variants = _title_variants(corpus_row.get("title"), corpus_row.get("subtitle"))
    return max(
        (
            SequenceMatcher(None, _normalize_text(source), _normalize_text(candidate)).ratio()
            for source in source_variants
            for candidate in corpus_variants
        ),
        default=0.0,
    )


def _author_last_names(author: Any) -> set[str]:
    """Return likely author surname tokens from a source author string."""
    text = _clean_cell(author)
    text = text.replace("/", " and ")
    text = re.sub(r"\bet\s+al\.?\b", "", text, flags=re.IGNORECASE)
    parts = re.split(r"\band\b|,|&", text, flags=re.IGNORECASE)
    suffixes = {"jr", "sr", "ii", "iii", "iv"}
    surnames: set[str] = set()
    for part in parts:
        tokens = [
            token
            for token in re.findall(r"[A-Za-z0-9]+", part)
            if token.lower() not in suffixes
        ]
        if tokens:
            surnames.add(tokens[-1].lower())
    return surnames


def _author_matches(source_author: Any, corpus_author: Any) -> bool:
    """Return True when any source surname appears in the corpus author text."""
    surnames = _author_last_names(source_author)
    if not surnames:
        return False
    corpus_tokens = set(re.findall(r"[A-Za-z0-9]+", _clean_cell(corpus_author).lower()))
    return bool(surnames & corpus_tokens)


def load_corpus(path: Path) -> list[dict[str, Any]]:
    """Read corpus rows with computed row numbers."""
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows: list[dict[str, Any]] = []
        for row_number, row in enumerate(reader, start=1):
            cleaned = {key: _clean_cell(value) for key, value in row.items()}
            cleaned["_corpus_row"] = str(row_number)
            rows.append(cleaned)
    return rows


def load_annotation_counts(path: Path) -> dict[str, int]:
    """Return annotation counts keyed by corpus_row."""
    if not path.exists():
        return {}
    counts: dict[str, int] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            corpus_row = _clean_cell(row.get("corpus_row"))
            if corpus_row:
                counts[corpus_row] = counts.get(corpus_row, 0) + 1
    return counts


def find_best_match(book: dict[str, Any], corpus_rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Return the best high-confidence corpus match for a sourced book."""
    candidates: list[tuple[float, int, int, dict[str, Any]]] = []
    for row in corpus_rows:
        title_score = _title_similarity(book["title"], row)
        author_ok = _author_matches(book["author"], row.get("author"))
        if title_score >= 0.88 and author_ok:
            candidates.append((title_score, 1, -int(row["_corpus_row"]), row))
    if not candidates:
        return None
    title_score, author_score, _, row = max(candidates, key=lambda item: item[:3])
    matched = dict(row)
    matched["_match_score"] = f"{title_score:.3f}"
    matched["_match_author_ok"] = "yes" if author_score else "no"
    return matched


def build_intake_rows(
    books: list[dict[str, Any]],
    corpus_rows: list[dict[str, Any]],
    annotation_counts: dict[str, int],
) -> list[dict[str, Any]]:
    """Attach reconciliation fields to sourced book records."""
    output_rows: list[dict[str, Any]] = []
    for book in books:
        match = find_best_match(book, corpus_rows)
        row = {key: _clean_cell(book.get(key)) for key in OUTPUT_COLUMNS}
        if match is None:
            row.update(
                {
                    "status": "needs_backfill",
                    "recommended_action": "Backfill into corpus, then annotate with content evidence.",
                    "already_annotated": "no",
                    "annotation_count": "0",
                    "notes": "No high-confidence corpus match found.",
                }
            )
        else:
            corpus_row = match["_corpus_row"]
            count = annotation_counts.get(corpus_row, 0)
            already_annotated = count > 0
            row.update(
                {
                    "corpus_row": corpus_row,
                    "corpus_title": match.get("title", ""),
                    "corpus_author": match.get("author", ""),
                    "match_score": match.get("_match_score", ""),
                    "match_author_ok": match.get("_match_author_ok", ""),
                    "corpus_source": match.get("source", ""),
                    "canonical_isbn_13": match.get("canonical_isbn_13", ""),
                    "already_annotated": "yes" if already_annotated else "no",
                    "annotation_count": str(count),
                }
            )
            if already_annotated:
                row["status"] = "already_annotated"
                row["recommended_action"] = "Skip for now unless doing audit/review."
                row["notes"] = "Existing annotations found in annotations_v1.csv."
            else:
                row["status"] = "ready_to_annotate"
                row["recommended_action"] = "Open annotate.py with this corpus_row after checking content evidence."
                row["notes"] = "Matched in corpus but not annotated yet."
        output_rows.append(row)
    return output_rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write intake rows atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in OUTPUT_COLUMNS})
    os.replace(tmp_path, path)


def main() -> int:
    """Build the reconciled top-50 intake CSV."""
    corpus = load_corpus(CORPUS_CSV)
    annotation_counts = load_annotation_counts(ANNOTATIONS_CSV)
    rows = build_intake_rows(TOP_50, corpus, annotation_counts)
    write_csv(rows, OUTPUT_CSV)

    status_counts: dict[str, int] = {}
    for row in rows:
        status = row["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    log.info("Loaded %d corpus rows", len(corpus))
    log.info("Loaded annotation counts for %d corpus rows", len(annotation_counts))
    log.info("Wrote %d intake rows to %s", len(rows), OUTPUT_CSV)
    for status, count in sorted(status_counts.items()):
        log.info("%s: %d", status, count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
