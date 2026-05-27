# Quantitative Edge Literature Survey — Retail Account ($1000)

**Date:** 2026-05-27
**Purpose:** Survey peer-reviewed and top-tier practitioner sources for retail-accessible quantitative edges; rank by evidence × tradability at $1000 with the QuiltTrader framework (Alpaca + Tradier + spot crypto).
**Status:** Reference document. Informs `2026-05-27-crypto-tsmom-research-program-design.md`.

---

## Source verification

| Source | Status |
|---|---|
| Liu & Tsyvinski, "Risks and Returns of Cryptocurrency," RFS 34(6):2689–2727, 2021 | Verified ([Oxford Academic](https://academic.oup.com/rfs/article-abstract/34/6/2689/5912024)) |
| Makarov & Schoar, "Trading and arbitrage in cryptocurrency markets," JFE 135(2):293–319, 2020 | Verified ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3171204)) |
| Hougan & Lawant, "Cryptoassets: The Guide…" CFA Inst. Research Foundation Brief, 2021 | Verified — practitioner brief, Hougan is CIO of Bitwise |
| Moskowitz, Ooi & Pedersen, "Time series momentum," JFE 104(2):228–250, 2012 | Verified ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463)) |
| Gatev, Goetzmann & Rouwenhorst, "Pairs Trading," RFS 19(3):797–827, 2006 | Verified ([Oxford Academic](https://academic.oup.com/rfs/article-abstract/19/3/797/1646694)) |

---

## Edge-by-edge review

### 1. Crypto funding-rate carry (long spot / short perp)

- **Canonical academic:** Schmeling, Schrimpf & Todorov, "Crypto carry," BIS WP 1087 (Apr 2023, rev. Oct 2025). [bis.org/publ/work1087.pdf](https://www.bis.org/publ/work1087.pdf)
- **Effect size:** Full-sample (2020–2025) Sharpe ≈ 6.45. **Falls to 4.06 in 2024 and turns negative in 2025.**
- **Replicated:** Yes — He, Manela et al., arXiv:2212.06888.
- **Faded?** **Yes, materially.** Sharpe has collapsed since 2024 as institutional flows competed away the spread. Post-FTX era removed forced-buyer sources.
- **$1000 retail accessibility:** **Hard.** Requires perp venue. Binance/Bybit/OKX geoblock US persons; Hyperliquid is DEX-only. CME futures contract size too large for $1000.
- **Confidence:** Medium-skeptical. Documented but post-2024 decayed; US retail access broken.

### 2. Crypto time-series momentum (BTC/ETH)

- **Canonical:** Liu & Tsyvinski, RFS 2021. Documents "a strong time-series momentum effect" in BTC at 1–6 week horizons.
- **Effect size:** Sample 2011–2018; positive 1–4 week returns predict 1–3% higher next-week return; t-stats > 2.5.
- **Replicated:** Han, Kang & Ryu, SSRN 4675565 (2024). TSMOM evidence strong, XSMOM evidence weak.
- **Hurst:** BTC Hurst was >0.6 pre-2017, converged toward 0.5 after 2017–18 bull (arXiv:1902.09253). Daily persistence weaker now; intraday persistence still documented.
- **$1000 retail accessibility:** **Easy.** Spot BTC/ETH on Coinbase/Kraken/Alpaca. No PDT. Slippage trivial at $1000.
- **Confidence:** **Medium-high.** Robust academic backing; expect real-world Sharpe ~0.4–0.8 after post-publication haircut.

### 3. Crypto cross-sectional momentum

- **Canonical:** Liu, Tsyvinski & Wu, "Common Risk Factors in Cryptocurrency," JF 77(2):1133–1177, 2022.
- **Effect size:** Winners-minus-losers weekly returns ~1.0% pre-cost.
- **Faded:** Likely. Han et al. (2024) find many momentum portfolios unprofitable after 30–80bps round-trip costs on alts.
- **$1000 retail accessibility:** **Poor.** Needs broad alt universe; position sizes ($10–20) below exchange minimum-order floors.
- **Confidence:** Low.

### 4. Crypto vol risk premium (Deribit options)

- **Canonical:** Alexander & Imeraj, "The Bitcoin VIX and Its Variance Risk Premium," J. Alt. Inv. 23(4):84–109, 2021.
- **Effect size:** Bitcoin VRP ≈ 14–15 vol points in contango.
- **Replicated:** "Risk Premia in the Bitcoin Market," arXiv:2410.15195 (2024).
- **$1000 retail accessibility:** **Bad for US.** Deribit geoblocks US. CME BTC options too large. IBIT/FBTC equity options on Alpaca/Tradier work but liquidity/skew differ; no peer-reviewed VRP work specifically on IBIT options yet.
- **Confidence:** Medium on edge; low on US-retail tradability.

### 5. Equity vol risk premium (SPX/SPY short premium)

- **Canonical:** Bollerslev, Tauchen & Zhou, "Expected Stock Returns and Variance Risk Premia," RFS 22(11):4463–4492, 2009. Bondarenko, "Historical Performance of Put-Writing Strategies," CBOE 2019.
- **Effect size:** Average VIX (19.3%) − realized vol (15.1%) ≈ 4.2 pts. CBOE PUT index Sharpe ≈ 0.50 (2006–2018).
- **Replicated:** Carr & Wu, RFS 2009; Bekaert & Hoerova, J. Econometrics 2014. One of the most robust anomalies in finance.
- **Faded:** Premium persists; tail risk severe (XIV Feb 2018, LJM, OptionSellers.com blowups).
- **$1000 retail accessibility:** **Workable via defined-risk structures.** Iron condors, put credit spreads on SPY. Multi-day hold dodges PDT.
- **Confidence:** **Medium-high on the edge, medium on tradability at $1000.**

### 6. Equity time-series momentum (managed-futures style)

- **Canonical:** Moskowitz, Ooi & Pedersen, JFE 2012.
- **Effect size:** Diversified portfolio Sharpe ≈ 1.4 (1985–2009).
- **Replicated with caveats:** Huang et al., JFE 2020 — much of Sharpe is vol-scaling, not pure trend.
- **Faded:** Partial. Post-publication CTA Sharpe ~0.3–0.5.
- **$1000 retail accessibility:** Hard via futures (contract sizes); workable via ETF proxies (DBMF, KMLM, or homebuilt sector ETF basket).
- **Confidence:** Medium.

### 7. Equity cross-sectional momentum

- **Canonical:** Jegadeesh & Titman, J. Finance 48(1):65–91, 1993. Retrospective: Jegadeesh & Titman, Pacific-Basin Finance Journal, 2023.
- **Effect size:** ~1%/month, t > 4 (original). 2023 update: "Momentum profits have remained large and significant in the three decades following our original study."
- **Replicated:** Extensively. One of few survivors of McLean & Pontiff post-publication haircut.
- **$1000 retail accessibility:** Practical via MTUM ETF; classic 20–50 name portfolio impractical at $1000 (tracking error swamps alpha).
- **Confidence:** High on edge; medium on retail capture.

### 8. Post-earnings announcement drift (PEAD)

- **Canonical:** Bernard & Thomas, J. Acc. Econ. 13(4):305–340, 1990. Recent: "PEAD.txt," Philly Fed WP 21-07.
- **Effect size:** Original decile spread ~5%/quarter; declined to ~3% by late 2010s; persists mainly in microcaps.
- **Faded:** Substantially in large-caps.
- **$1000 retail accessibility:** PDT-safe (multi-day hold). But microcap edge requires names with worst slippage.
- **Confidence:** Medium-low.

### 9. Pairs trading

- **Canonical:** Gatev, Goetzmann & Rouwenhorst, RFS 2006.
- **Effect size:** 11% annualized excess (1962–2002).
- **Replicated update:** Do & Faff, FAJ 2010 — excess fell from 0.86%/mo → 0.24%/mo by 2003–2009. Do & Faff JFR 2012: largely unprofitable post-2002 after costs.
- **Confidence:** **Skeptical / low.** Classic version is dead at retail size.

### 10. Options anomalies

- **Weekend theta:** No peer-reviewed support. Treat as folk wisdom.
- **VIX term structure:** Roll yield short-VXX-long-VIXY had Sharpe ~1; destroyed in Feb 2018 (XIV).
- **Skew (Bollen & Whaley, JF 2004; Garleanu, Pedersen, Poteshman, RFS 2009):** Persists; same trade as #5 (equity VRP).

---

## McLean & Pontiff context

McLean & Pontiff (JF 2016): 97 predictors decline **26% out-of-sample, 58% post-publication.** Haircut every Sharpe above by ~50% before sizing.

---

## Top 3 candidates for $1000 retail

Ranked on (evidence) × (post-publication survival) × ($1000 tradability with QuiltTrader framework):

| Rank | Edge | Why | Framework status |
|---|---|---|---|
| 1 | **Crypto time-series momentum (BTC/ETH spot)** | Peer-reviewed; replicated; no PDT; minimal slippage at $1000; 14yr free daily history | Spot crypto already supported via Alpaca/Coinbase |
| 2 | **Equity VRP via defined-risk SPX/SPY credit spreads** | Most rigorously documented edge; defined-risk fits $1000 | Options support exists; cost model needs spread modeling |
| 3 | **Cross-sectional momentum via MTUM ETF** | Survived 2023 J&T retrospective explicitly | Equity support exists; monthly rebalance dodges PDT |

### Do not pursue
- Crypto cash-and-carry (decayed since 2024, US access broken)
- Classic pairs trading (decayed since 2002, dead at retail size)
- Naked short vol (tail risk + capital requirements)
- Weekend theta (no peer-reviewed support)

---

## Sources

- Liu & Tsyvinski 2021 RFS — https://academic.oup.com/rfs/article-abstract/34/6/2689/5912024
- Liu, Tsyvinski & Wu 2022 JF — https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13119
- Makarov & Schoar 2020 JFE — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3171204
- Moskowitz, Ooi & Pedersen 2012 JFE — http://docs.lhpedersen.com/TimeSeriesMomentum.pdf
- Gatev, Goetzmann & Rouwenhorst 2006 RFS — https://academic.oup.com/rfs/article-abstract/19/3/797/1646694
- Schmeling, Schrimpf & Todorov, BIS WP 1087 — https://www.bis.org/publ/work1087.pdf
- He, Manela et al. "Fundamentals of Perpetual Futures" — https://arxiv.org/html/2212.06888v5
- Han, Kang & Ryu, SSRN 4675565 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565
- Alexander & Imeraj, Bitcoin VIX and VRP — https://www.pm-research.com/content/iijaltinv/23/4/84
- Bondarenko CBOE Put-Write 2019 — https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf
- Jegadeesh & Titman 2023 retrospective — https://www.sciencedirect.com/science/article/abs/pii/S0927538X23002731
- Do & Faff 2010 FAJ — https://www.tandfonline.com/doi/abs/10.2469/faj.v66.n4.1
- McLean & Pontiff 2016 JF — https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365
- Bollen & Whaley 2004 JF — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=319261
- PEAD.txt, Philly Fed WP 21-07 — https://www.philadelphiafed.org/-/media/frbp/assets/working-papers/2021/wp21-07.pdf
- Bitcoin Hurst — https://arxiv.org/pdf/1902.09253
- Risk Premia in Bitcoin — https://arxiv.org/html/2410.15195v2
