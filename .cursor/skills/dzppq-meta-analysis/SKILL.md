---
name: dzppq-meta-analysis
description: Generate DZPPQ meta and environment analysis reports from the latest match SQLite database. Use when analyzing current meta, strong comps, carries, cards, equipment, traps, balance changes, or when the user mentions 对局db、环境分析、阵容推荐、卡牌强度、成型难度、版本陷阱.
---

# DZPPQ Meta Analysis

## Required Workflow

When the user asks for a DZPPQ meta/environment report:

1. Read this file, [intro.md](intro.md), and [report-spec.md](report-spec.md).
2. Resolve the match database:
   - Use the user-provided DB path when present.
   - Otherwise use `data/match_latest.db`.
   - If that file is missing, fall back to the newest `data/matches_*.db`.
   - If no DB exists, tell the user to build/import the latest DB first.
3. Run the built-in analyzer before writing conclusions:

```bash
python .cursor/skills/dzppq-meta-analysis/scripts/analyze_latest_meta.py
```

Use `--db <path>` when the user provides a database. If the user provides balance notes in a file, pass `--balance-notes <path>`. Recent-environment weighting uses batch date from `matches.path` (`screenshots.MMDD`), not screenshot capture time.

4. Base the final answer on `data/环境分析详情.html`, `data/latest_meta_analysis_report.md`, `data/latest_meta_analysis.json`, `data/latest_meta_analysis_equipment.xlsx`, and per-hero pages under `data/hero-equipment/`.
5. Mention data quality caveats: sample size, unknown labels, excluded bot records, and low-confidence segments.

Per-hero equipment tables are exported to Excel and standalone HTML pages under `data/hero-equipment/`. The interactive dashboard and Markdown report keep a short carry overview and link to those files. Use panel hash anchors such as `#equipment`, `#super-equipment`, `#food-equipment`, `#compositions`, and `#primary-bond` to jump to panels inside `data/环境分析详情.html`. Clicking a hero name in the equipment panel opens that hero's detail page in a new browser tab.

Do not use older report files as the primary source. Older scripts in `scripts/` and `src/meta_analysis.py` are historical references only unless the user explicitly asks to compare with them.

## Mandatory Data Rules

- Exclude rank 7 and rank 8 players when they are teammates in the same match; treat them as bots.
- Exclude `unknown` heroes, cards, and equipment from reference statistics.
- Read hero cost and bonds from `config_s2.py`.
- A bond item named `X啾啾` adds 1 count to bond `X` only when `X` exists in `dict_bond`.
- Normalize `核选X` and `X` as the same equipment, while keeping selected-rate metrics for upgrade priority.
- Super equipment is the fixed whitelist: `巫术玩偶` `小鲨包` `金咸鱼` `幸运猫猫` `碰碰气球` `炸炸魔术箱` `发财树` `核桃火箭` `鲱鱼罐头`.
- Food-club equipment includes normalized names starting with `美味` / `绝味` / `暗黑`, plus exact names `杏仁豆腐` `椒盐酥糖` `岛好锅`; keep `岛好锅` but force low-confidence notes when samples are scarce.
- Main carry judgment must follow player investment: more equipment, more selected equipment, higher stars, and earlier board slot. Export the top 3 carry candidates per board with explicit priority (`P1`/`P2`/`P3`).
- Card order is preserved by `slot_index`; the first card (`cards[0]`) is the duo-focused card.
- Team rank is recomputed per match: sort teams by their best individual rank to get team rank 1-4.
- Blue cards are duo-oriented; report their normal holder performance plus a team-rank view.
- Card rankings are sorted by sample count first within each prefix group, and include average appearances per match.
- Exclude card-granted heroes such as `暴龙虾饺` from lineup level and representative lineup lists.
- Label scattered first-tier trait boards as `拼多多` unless a stable activated carry trait clearly leads the comp.
- Play-style hard rules: any lineup 1/2/3-cost 3-star unit is `赌狗`; `level <= 6` is `赌狗`; level-7 boards with a low-cost main carry are `赌狗`; only boards without low-cost 3-stars can be `高费`.
- Split composition recommendations into only `赌狗` and `高费` using those play-style rules. Strategy recommendation buckets follow the mature-stage play style, not aggregate majority across transition boards; keep `play_style_breakdown` auditable.
- If mature-stage `carry_requirements` ask for any 1/2/3-cost unit at 3 stars, force the strategy into `赌狗`. High-cost recommendations must not publish low-cost 3-star star gates.
- Include every discovered strategy that already passed the clustering discovery threshold (`min_comp_apps`, default 5). Do not apply a per-style count limit.
- Classify gameplay archetypes separately from factual `main_bond`: `美食社收菜` (food harvest equipment), `高费拼多多` (high-cost scattered traits with no low-cost 3-star and a 2-star+ 4/5-cost main carry), `羁绊运营:X`, or `拼多多`. Keep archetype as detail evidence, not as a recommendation filter dimension.
- Confidence evidence (`raw_n` / weighted n / `n_eff` / batch coverage / cluster purity / observed wins / play-style top4 lower bound / normal-cost ceiling) remains for audit and low-confidence notes; it does not create an `观察` queue or block inclusion.
- Mature-stage stats drive ranking and detail performance; transition-stage failures contribute to formation difficulty only. Render mature/transition inversion diagnostics when a higher-tier stage is rejected for worse performance.
- Do not emit separate `高费大成上限` / `高费大成上限观察` recommendation keys or report sections. High-cost 3-star dependency still caps normal star advice at 2 stars and shows a cost-risk note on the affected high-cost comps.
- Version trend labels (`上升` / `稳定` / `下滑`) compare recent vs prior batch windows; insufficient samples must not force a trend call.
- Blue cards `蓝·一起刷刷刷` and `蓝·天降啾啾pro` share an icon but are disambiguated by final-board jiujiu equipment count (`>= 2` -> pro); never report them as one merged card statistic.
- Yellow cards `黄·巨神兵` and `黄·迅迅迅捷双剑` share an icon but are disambiguated by final-board equipment counts of `巨神兵之斧` / `迅捷双剑` (axe-only -> 巨神兵, sword-only -> 迅迅迅捷双剑, both present -> majority); equal counts (including both zero) are assigned from the clear-sample ratio of the current database with a fixed seed. Never report them as one merged card statistic.
- Do not recommend 3-star 4-cost or 5-cost carries as a normal requirement.
- Primary bond strength uses business classification: food harvest -> `美食社`, factual bonds must reach the configured second threshold, ties by activation count are retained, and `高费拼多多` is the fallback when no factual bond qualifies.
- Merge duplicate comp rows into strategy-level recommendations with mature stages and transition stages.
- Count cross-strategy contest pressure when strategies need the same 3-star main carry, even if their final bonds differ.
- Treat weak lower-tier bond rows covered by a strong mature strategy as formation pressure, not standalone version traps.
- Evaluate jiujiu only when it contributes as a final main/sub bond, specific hero boost, or cross-strategy generalist value.
- Weight recent batches more heavily using `screenshots.MMDD` from `matches.path`; keep raw sample counts for confidence thresholds.

## Output Expectations

Write concise Chinese conclusions. Include:

- Separate `赌狗` and `高费` comp recommendations with concrete 7/8/9-level hero lists when data supports them. Output all discovered strategies in each style. True `高费拼多多` comps rely on 2-star 4/5-cost carries without low-cost 3-stars.
- Show archetype labels (`美食社收菜`, `高费拼多多`, etc.), archetype evidence (including low-cost 3-star count and main-carry cost/stars for high-cost PDD), mature vs transition performance, inversion diagnostics, low-confidence notes, trend, raw/weighted/`n_eff`, confidence evidence, score breakdown, and cluster/merge reasons in Markdown and HTML comp panels.
- Interactive HTML dashboard at `data/环境分析详情.html` with tabbed panels for comp details, equipment, super equipment, food equipment, cards, jiujiu, traps, duo synergy, low-cost carry difficulty, and primary bond strength.
- Composition panel filters are only `全部` / `赌狗` / `高费`; do not add zone or archetype filter rows.
- Excel equipment workbook at `data/latest_meta_analysis_equipment.xlsx` for per-hero and per-comp equipment detail, plus super/food equipment rankings.
- Hero equipment recommendations split normal items, `recommended_super_items`, and `recommended_food_items`; dedicated dashboard panels rank each special class with recommended wearers.
- Per-hero equipment detail (`detail_items`) lists every single item with raw `appearances > 10`, including normal/super/food kinds and weighted metrics. Each such hero gets a standalone page under `data/hero-equipment/` opened via `target="_blank"`.
- Carry analysis for each recommended comp, including top 3 carries with priority.
- Card strength, with composition-specific notes when sample size allows.
- First-card duo synergy and contribution observations based on recomputed team rank.
- Formation difficulty and popularity with overall strength ranking.
- Low-cost 3-star main-carry difficulty, including same-match demand pressure by hero.
- Duo composition synergy recommendations based on recomputed team rank.
- Strong carry heroes overview in Markdown; full equipment recommendations in Excel, filterable HTML, and standalone hero pages.
- Main carry star requirement (average top4 stars) and key equipment dependency notes for recommended comps.
- Jiujiu dependency notes when a comp title bond requires `X啾啾`, including recommended wearer heroes.
- Jiujiu strength ranking and recommended comps for each jiujiu item.
- Jiujiu recommended comps should include observed wearer heroes when available.
- Version traps: popular but weak heroes, comps, bonds, equipment, or cards.
- Primary bond strength ranking: business-classified bonds with `source` / `category` audit fields; food harvest -> `美食社`, second-threshold factual bonds, `高费拼多多` fallback.
- Balance-change tracking when the user provides patch notes.

If a section is low confidence, keep it in the report but label it clearly instead of hiding it.
