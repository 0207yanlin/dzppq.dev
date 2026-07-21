# DZPPQ Meta Report Spec

## Inputs

Primary input is a SQLite DB matching `src/match_db.py`:

- `matches.path`: batch folder such as `screenshots.0701/...`; this is the source of truth for batch date.
- `matches.match_date`: stored `MMDD` batch key parsed from `path`.
- `matches.captured_at`: screenshot timestamp only; do not use for recency weighting.
- `players.rank`: lower is better.
- `heroes.slot_index`: lower means earlier board position.
- `heroes.stars`, `heroes.equipment_count`, `hero_equipments.equipment_name`.
- `cards.card_name`.
- `pairs` and `players.partner_player` for bot and teammate rules.
- Card rows are ordered by `slot_index`; the first card is `slot_index == 0`.

`config_s2.py` is the only source for hero tier, hero bonds, and bond thresholds.

## Filtering

Apply filtering before all rankings:

- Exclude rank 7/8 players when they are paired in the same match.
- Exclude `unknown` entities from their own statistics.
- Exclude card-granted heroes such as `暴龙虾饺` from lineup level, representative lineup lists, and displayed core heroes.
- Keep a data-quality summary with raw counts and excluded counts.
- Keep low-sample rows only when useful for discovery, and mark confidence as low.
- Weight recent batches more heavily using `matches.match_date` / `screenshots.MMDD` from `path`.
- Use exponential decay with default half-life 2 days and minimum weight 0.25.
- Keep raw `appearances` for minimum-sample thresholds; use weighted values for avg rank, top4, win rate, popularity share, and sorting.

## Feature Rules

### Equipment

- Normalize equipment names by removing the `核选` prefix.
- Store `selected_count` and `selected_rate` separately.
- Equipment upgrade priority is higher when selected-rate is high and rank impact is positive.
- Super equipment whitelist: `巫术玩偶`, `小鲨包`, `金咸鱼`, `幸运猫猫`, `碰碰气球`, `炸炸魔术箱`, `发财树`, `核桃火箭`, `鲱鱼罐头`.
- Food-club equipment: normalized names starting with `美味` / `绝味` / `暗黑`, plus exact names `杏仁豆腐`, `椒盐酥糖`, `岛好锅`.
- Special equipment rankings sort by mature/high-sample preference then adjusted avg rank / top4; low-sample rows stay visible with `低` confidence. `岛好锅` must carry an explicit low-confidence warning when scarce.
- Per-hero recommendations expose `recommended_items` (normal), `recommended_super_items`, and `recommended_food_items` without duplicating special items into the normal column.

### Bonds

- Count each known hero bond once per hero.
- For equipment ending in `啾啾`, add 1 to the matching bond if the bond exists in `dict_bond`.
- Active tier is the highest threshold satisfied by the final count.

### Primary Bond Strength

Separate from composition `main_bond` and from activated bond-tier rows in `heroes_and_equipment.bonds`:

- Business classification is separate from factual `PlayerFeature.main_bond`.
- Study club at the configured tier-4 threshold (`dict_bond["学习社"][2]`, currently 4) exclusively maps to `学习社`, covering food harvest and every other business category.
- Otherwise, food-harvest equipment or `美食社收菜` archetype boards are assigned to `美食社`.
- Otherwise, only factual bonds that reach the configured second threshold qualify.
- Qualified bonds are ranked by final activation count (`trait_totals`, including jiujiu bonus); ties at the max count are all retained.
- If no factual bond qualifies and the archetype is `高费拼多多`, classify as `高费拼多多`.
- Aggregate statistics by bond/category name only; do not split `学习社3` and `学习社5` into separate rows.
- Keep per-row `source_distribution` and overall `source` / `category` audit fields in Markdown and HTML.
- Rank bonds by average rank ascending, then top4 rate descending, then sample size.
- Keep existing bond-tier and jiujiu sections unchanged.

### Carry Score

Score every hero in a player board:

```text
carry_score =
  equipment_count * 30
  + selected_equipment_count * 12
  + stars * 10
  + tier * 2
  + max(0, 8 - slot_index) * 1.5
```

The top score is the main carry. Nearby scores can be secondary carry or frontline depending on equipment and hero role inferred from item patterns. Always explain this is investment-based, not a hard game-role label.

## Composition Detection

Represent each player board as:

- `hero_set`: all known heroes.
- `level`: number of known heroes, capped for reporting as 7/8/9.
- `main_bond`: active bond with highest tier, breaking ties by count and name.
- `archetype`: gameplay identity separate from factual bonds (`美食社收菜`, `高费拼多多`, `羁绊运营:X`, `拼多多`).
- `archetype_signals`: auditable evidence for the archetype label.
- `carry_names`: top 3 carry-score heroes.
- `comp_signature`: main bond plus carry names.

Cluster boards into comp families:

- Start from top-half boards and high-confidence boards.
- Two boards are similar when Jaccard(hero_set) >= 0.55, or when they share the same main bond and at least one carry.
- Merge small neighboring groups when they share core carries and main bond.
- Do not force every board into a meta comp; leave noisy boards as mixed.
- Name comp families from family-level trait distribution, not a single-board vote.
- If activated traits are mostly first-threshold traits and no stable carry trait leads, label the comp as `拼多多 / carry1+carry2`.
- Any final board with normalized food-harvest equipment (`美味` / `绝味` / `暗黑` prefixes, including `核选`) is a strong `美食社收菜` signal; clustering and strategy naming should preserve that archetype even when factual bonds differ.
- High-cost boards with enough 4/5-cost units, no low-cost 3-star unit, a 2-star+ 4/5-cost main carry, and no stable deep trait investment should be labeled `高费拼多多`; they remain in the `高费` recommendation pool.
- Force the label bond into `common_bonds` when a bond label is used.
- Split large families into high-tier bond subfamilies when sample size is sufficient, e.g. `考古社-7`.
- Merge duplicate comp rows into strategy-level models when they share core carries and compatible main/sub bond traits. Keep lower-tier forms as `transition_stages` under the mature strategy.

For each comp family, report:

- Representative 7/8/9-level hero lists from best-ranked samples, plus per-variant bond attainment notes.
- Representative samples should prefer boards that actually satisfy the title bond at that level; if none exist, mark the bond as unmet instead of implying completion.
- Main carry and up to two alternate carries, with explicit priority ranking.
- Main carry minimum star recommendation, average top4 stars, and three-item coverage.
- For 4-cost and 5-cost carries, cap normal star recommendation at 2 stars. If 3-star high-cost samples dominate, mark the comp as high-cost ceiling dependent instead of recommending 3 stars.
- Key carry equipment: mark only sufficiently supported items as required; otherwise label as high-value or observation.
- Jiujiu dependency when the title bond tier requires `X啾啾`: dependency rate, recommended jiujiu item, and recommended wearer heroes.
- Core bond tiers and common sub-bonds.
- Average rank, top4 rate, win rate, sample size, confidence.
- Average number of 3-star lineup units overall and in top4 samples.
- Mature stage and transition stages. Transition stages contribute to formation difficulty and trap analysis but should not become separate top-level recommendations when they are the same strategy.
- Strategy rows should expose `mature_stats`, `transition_stats`, `aggregate_stats`, `cluster_reason`, `merge_reason`, `confidence_evidence`, `score_breakdown`, `stage_inversion_diagnostics`, and `trend` when available.
- Markdown/HTML comp panels must show low-confidence notes when formal confidence criteria fail, plus mature/transition inversion diagnostics when present.

### High-Cost Three-Star Risk

- Do not emit separate `高费大成上限` / `高费大成上限观察` recommendation keys or report sections.
- When a 4/5-cost carry shows high 3-star dependency, cap the normal recommended star requirement at 2 stars and show an explicit cost-risk note on that high-cost comp.
- Keep the underlying `high_cost_three_star_dependency` flag on composition rows for audit and scoring penalties.

### Recommendation Thresholds

- Discovery threshold: `min_comp_apps=5` keeps a comp family visible and eligible for the recommendation list.
- Formal confidence criteria remain auditable on each strategy:
  - raw `appearances >= 10`
  - weighted appearances `>= 5`
  - effective sample `n_eff >= 8`
  - batch coverage `>= 2`
  - cluster archetype purity `>= 0.70`
  - observed wins `>= 1`
  - shrunk top4 lower bound within play-style baseline gap
  - no high-cost three-star dependency for normal star advice
- Failing the formal confidence criteria must not remove the strategy from `赌狗` / `高费` recommendations. Render a low-confidence note instead.
- There is no per-style count limit; output every discovered strategy under its play style.
- Recommendation ranking uses shrunk performance, formation difficulty, uncertainty, and version trend; popularity is descriptive only.

### Version Trend Windows

- Default trend mode compares the latest 2 batches vs the prior 2 batches using `screenshots.MMDD` batch order with cross-year handling.
- If a supported balance boundary is provided, compare post-boundary vs pre-boundary windows instead of only rolling windows.
- Output `上升`, `稳定`, `下滑`, or insufficient-sample status; do not label random noise as a version shift.
- Trend fields belong on strategy rows and should be rendered in Markdown/HTML comp panels.

### Classification Overview

| Dimension | Values | Use |
|-----------|--------|-----|
| play_style | `赌狗` / `高费` | Recommendation sections, JSON keys, dashboard filters |
| archetype | `美食社收菜` / `高费拼多多` / `羁绊运营:X` / `拼多多` | Naming and detail evidence only |
| low-cost 3-star signal | any lineup 1/2/3-cost unit at 3 stars | Forces `赌狗`; blocks `高费拼多多` |

### Play Style Split

Every player board and merged strategy should be classified as either `赌狗` or `高费`.

- Any lineup 1/2/3-cost 3-star unit: always `赌狗`.
- `level <= 6`: always `赌狗`.
- `level == 7` with a low-cost main carry: `赌狗`.
- Otherwise, boards without low-cost 3-stars are `高费`.

Keep a `play_style_breakdown` on strategy rows because merged strategies can contain mixed stage samples. Recommendation sections should be split by final strategy `play_style`, which follows the mature stage rather than aggregate majority across transition boards. If mature-stage carry advice requires a low-cost 3-star, force `赌狗`. Global difficulty, popularity, trap, hero, equipment, and card analysis can still use all strategies.

## Formation Difficulty And Popularity

Difficulty combines:

- Unfinished pressure: similar low-star or low-equipment versions in bottom four.
- Contest pressure: average number of similar boards in the same match.
- Cross-strategy 3-star carry pressure: if two strategies require the same 3-star main carry, count them as contesting each other even when their final bonds differ.
- Key-unit pressure: missing or under-starred main carry in poor results.
- Equipment pressure: average carry equipment completeness.

Popularity combines:

- Player share among filtered records.
- Match share where at least one similar board appears.
- Average contest count per match.

Use labels: low, medium, high. Explain when a comp is strong but hard to complete or popular enough to be contested.

Also publish an overall strength rank per strategy that combines post-formation performance (avg rank, top4, win rate) with formation difficulty (unfinished pressure, contest pressure, equipment completeness).

Also publish low-cost 3-star main-carry difficulty:

- required 3-star carry hero, tier, appearances, average same-match needers, max same-match needers.
- match rate where multiple players need the same hero at 3 stars.
- top strategies that require the hero.

## Card Analysis

Primary card ranking:

- single-card appearances, avg rank, top4 rate, win rate.
- adjusted rank with global baseline and a small prior to reduce low-sample noise.
- average appearances per match.
- within each prefix group, sort primary card rankings by sample count first, then adjusted rank and outcome metrics.
- first-card rankings use `cards[0]`.
- group single-card and first-card rankings by card template prefix type (`彩` / `黄` / `蓝` / `白` / `其他`) and rank within each group; do not compare different prefix types on one leaderboard.
- unprefixed disambiguated labels such as `吸吸宝pro` and `快速成型` should inherit their source prefix type for grouping; `蓝·重质也重量pro` and `蓝·拍档支援` keep their template prefix directly.
- `蓝·一起刷刷刷` and `蓝·天降啾啾pro` share one icon template but must be resolved separately: count final-board equipment names ending in `啾啾`; `>= 2` resolves to `蓝·天降啾啾pro`, otherwise `蓝·一起刷刷刷`. Legacy merged labels are input aliases only; rankings and reports must not call them one merged statistic.
- `黄·巨神兵` and `黄·迅迅迅捷双剑` share one icon template but must be resolved separately: count final-board `巨神兵之斧` / `迅捷双剑` (optional `核选` prefix stripped); axe-only -> `黄·巨神兵`, sword-only -> `黄·迅迅迅捷双剑`, both present -> majority count; equal counts (including both zero) use the clear-sample ratio from the current database with a fixed seed. Legacy/merged labels are input aliases only; rankings and reports must not call them one merged statistic.
- blue cards are duo-oriented and should additionally include a recomputed team-rank view with team average rank and team top2 rate.

Secondary card views:

- per-comp card performance when sample size is sufficient.
- two-card and three-card combinations only as observation rows.
- first-card duo synergy compares both teammates' first cards.
- Team rank is recomputed per match: each team takes its best individual rank, then all teams are sorted to produce team rank 1-4.
- Duo card contribution includes holder avg rank, recomputed team avg rank, lift versus team baseline, and lift versus holder rank after scaling individual rank to team-rank range.
- Duo composition synergy compares the two teammates' final strategy labels using recomputed team rank, team top2 rate, and team win rate.
- teammate pair/card synergy only as low-confidence observations unless sample size is strong.

## Strong Heroes And Equipment

Strong heroes should prioritize heroes that are repeatedly identified as main carry or secondary carry.

For each hero:

- carry appearances, avg rank, top4 rate, win rate.
- common equipment, normalized by selected/non-selected name.
- selected-rate and selected-priority.
- required/high-value equipment notes when with-item performance clearly beats without-item performance.
- Sort main equipment recommendations by sample size first, then adjusted rank/top4. Low-sample high-roll items belong in an observation note.
- common 3-item sets when enough complete samples exist.

Export the full per-hero equipment tables to `data/latest_meta_analysis_equipment.xlsx`. The Markdown report should only keep a short high-investment carry overview and link to the Excel file, the dashboard equipment panel, and standalone hero pages under `data/hero-equipment/`.

Also publish:
- Super equipment strength ranking with recommended wearer heroes.
- Food-club equipment strength ranking with recommended wearer heroes.
- Markdown anchors back to `#super-equipment` and `#food-equipment`.
- Per-hero `detail_items`: every single equipment with raw `appearances > 10` across normal/super/food kinds, including weighted appearances, `n_eff`, weighted avg rank / top4 / top2 / win rate, adjusted avg rank, and selected rate. Summary recommendation columns remain truncated and use their own reliability thresholds.
- Standalone hero pages: Unicode filenames such as `data/hero-equipment/厨师长.html`, linked from the dashboard with URL-encoded hrefs and `target="_blank" rel="noopener noreferrer"`.

## Jiujiu Analysis

For every observed `X啾啾`:

- classify each sample as `final_bond`, `hero_boost`, `generalist`, or `incidental`.
- A jiujiu is effective when it is part of the final main/sub bond, significantly boosts a specific carry/frontline/key unit, or shows stable positive value across multiple strategies.
- Rank the item by effective sample count, effective rate, adjusted avg rank, avg rank, top4 rate, and win rate.
- Recommended comps should only come from `final_bond` or stable `generalist` evidence. `hero_boost` recommendations should name specific heroes instead of forcing a comp.
- Recommended comp rows should include observed wearer heroes for that jiujiu item when available.
- Keep tier-uplift data as supporting evidence only; uplift alone does not prove strength.

## Strategy-Level Traps

Version traps are evaluated after merging mature and transition stages:

- Aggregate all stages of the same strategy and dedupe players before judging.
- Weak transition stages are formation difficulty, not traps, when the mature stage is strong.
- A strategy is a trap only when the full strategy remains popular and weak after considering mature plus transition results.
- Weak lower-tier bond rows covered by a strong mature strategy, such as a low-tier learning-club board that belongs to a strong higher-tier learning-club strategy, should be reported as formation pressure rather than independent bond traps.

## Version Traps

A trap must satisfy both:

- meaningful popularity: sample size or pick rate above the report threshold.
- weak outcome: poor adjusted avg rank, low top4 rate, or poor result compared with similar-cost baseline.

Detect traps for heroes, comp families, bonds, equipment, and cards. Do not label a low-sample weak row as a trap; call it low-confidence instead.

## Balance Notes

When balance notes are provided:

- Extract mentioned heroes, bonds, equipment, and cards by exact string matching against config and observed DB names.
- Add a tracking section with pick rate, avg rank, top4 rate, carry rate, and equipment/card changes for mentioned entities.
- If sample size is low, report the direction but mark confidence low.

## Report Template

Use this Markdown shape:

```markdown
# 蛋仔派对当前环境分析报告

## 数据概览与过滤摘要
## 当前环境结论摘要
## 赌狗阵容推荐
## 高费阵容推荐
## 阵容成型难度与热门程度
## 卡牌强度分析
## 强势棋子与装备推荐
## 主羁绊强度
## 羁绊表现与啾啾影响
## 版本陷阱分析
## 平衡性调整追踪
## 数据质量与可信度说明
```

## Interactive HTML Dashboard

Also write `data/环境分析详情.html`:

- one tabbed dashboard with clickable top-level panels.
- default panel order: composition recommendations, primary bond strength, equipment, super equipment, food equipment, card prefix tables, duo synergy, low-cost 3-star carry difficulty, jiujiu dependency/wearer tables, trap compositions.
- support hash navigation such as `#equipment`, `#super-equipment`, `#food-equipment`, `#compositions`, and `#primary-bond`.
- sortable tables must show the active sort field and direction (`当前按 xxx 升序/降序`).
- equipment panel keeps cost/trait/search filters and sortable columns, plus separate super/food recommendation columns; default `全部` filters stay neutral via CSS `.active[data-*="all"]`, and only concrete selections use the golden active style via CSS `:not([data-*="all"])`.
- dashboard CSS declares `color-scheme: dark` and uses muted table/header/chip surfaces so equipment and jiujiu panels stay dark on first paint, not only after filtering.
- sticky table headers use `th { top: 0 }` so they stick to the `.table-wrap` top; do not use a positive viewport offset such as `top: 64px`, which covers the first data row inside the horizontal scroll container.
- equipment hero names open standalone pages under `data/hero-equipment/` in a new browser tab; do not embed all per-hero detail sections into the dashboard.
- super/food equipment panels show strength rank, sample metrics, confidence, recommended wearers, and low-sample notes.
- composition panel keeps paginated comp detail pages with 7/8/9 board cards and only `赌狗/高费` style filters.
- do not render zone filters, archetype filters, observation queues, or high-cost ceiling recommendation pages in the dashboard.
- comp detail cards should show archetype, archetype evidence, mature/transition stats, inversion diagnostics, low-confidence notes, trend, raw/weighted/`n_eff`, confidence evidence, score breakdown, and cluster/merge reasons when present.
- primary-bond panel should show business classification rules plus `source` / `category` audit fields.
- trap panel keeps trap comp cards with 7/8/9 observed boards.
- do not write separate poster HTML or standalone per-table HTML files, except the intentional per-hero equipment pages under `data/hero-equipment/`.

## Excel Equipment Output

Also write `data/latest_meta_analysis_equipment.xlsx`:

- sheet `全英雄出装`: per-hero recommended normal items with rank order and selected-equipment priority.
- sheet `英雄超级装备推荐`: per-hero super equipment recommendations.
- sheet `英雄美食社装备推荐`: per-hero food-club equipment recommendations.
- sheet `超级装备排行`: super equipment strength ranking and recommended wearers.
- sheet `美食社装备排行`: food-club equipment strength ranking and recommended wearers.
- sheet `阵容主C关键装备`: comp-scoped carry equipment notes.
- sheet `常见三件套`: common 3-item sets.
- sheet `低样本观察`: low-sample equipment observations.

Requires `openpyxl`.
