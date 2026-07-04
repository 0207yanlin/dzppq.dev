# DZPPQ Meta Report Spec

## Inputs

Primary input is a SQLite DB matching `src/match_db.py`:

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

## Feature Rules

### Equipment

- Normalize equipment names by removing the `核选` prefix.
- Store `selected_count` and `selected_rate` separately.
- Equipment upgrade priority is higher when selected-rate is high and rank impact is positive.

### Bonds

- Count each known hero bond once per hero.
- For equipment ending in `啾啾`, add 1 to the matching bond if the bond exists in `dict_bond`.
- Active tier is the highest threshold satisfied by the final count.

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
- `carry_names`: top 3 carry-score heroes.
- `comp_signature`: main bond plus carry names.

Cluster boards into comp families:

- Start from top-half boards and high-confidence boards.
- Two boards are similar when Jaccard(hero_set) >= 0.55, or when they share the same main bond and at least one carry.
- Merge small neighboring groups when they share core carries and main bond.
- Do not force every board into a meta comp; leave noisy boards as mixed.
- Name comp families from family-level trait distribution, not a single-board vote.
- If activated traits are mostly first-threshold traits and no stable carry trait leads, label the comp as `拼多多 / carry1+carry2`.
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

### Play Style Split

Every player board and merged strategy should be classified as either `赌狗` or `高费`.

- `level <= 6`: always `赌狗`.
- `level >= 8` with no 1/2/3-cost 3-star unit: `高费`.
- `level == 7`: low-cost main carry means `赌狗`; otherwise `高费`.
- Remaining edge cases: low-cost 3-star main carry means `赌狗`; otherwise `高费`.

Keep a `play_style_breakdown` on strategy rows because merged strategies can contain mixed stage samples. Recommendation sections should be split by final strategy `play_style`, while global difficulty, popularity, trap, hero, equipment, and card analysis can still use all strategies.

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

Export the full per-hero equipment tables to `data/latest_meta_analysis_equipment.xlsx`. The Markdown report should only keep a short high-investment carry overview and link to the Excel file.

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
## 羁绊表现与啾啾影响
## 版本陷阱分析
## 平衡性调整追踪
## 数据质量与可信度说明
```

## HTML Poster Output

Also write `data/latest_meta_analysis_report.html`:

- fixed-width poster around 1080px, designed for a 3:4 screenshot.
- show sample summary, top strategy comps split by `赌狗`/`高费`, mature lineup, deduped transition path, top 3 carry requirements, jiujiu dependency notes, card picks, jiujiu highlights, and structured traps.
- do not mirror the full Markdown report; keep the HTML concise and visual.

## Excel Equipment Output

Also write `data/latest_meta_analysis_equipment.xlsx`:

- sheet `全英雄出装`: per-hero recommended items with rank order and selected-equipment priority.
- sheet `阵容主C关键装备`: comp-scoped carry equipment notes.
- sheet `常见三件套`: common 3-item sets.
- sheet `低样本观察`: low-sample equipment observations.

Requires `openpyxl`.
