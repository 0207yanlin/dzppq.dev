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
- `carry_names`: top 1-2 carry-score heroes.
- `comp_signature`: main bond plus carry names.

Cluster boards into comp families:

- Start from top-half boards and high-confidence boards.
- Two boards are similar when Jaccard(hero_set) >= 0.55, or when they share the same main bond and at least one carry.
- Merge small neighboring groups when they share core carries and main bond.
- Do not force every board into a meta comp; leave noisy boards as mixed.

For each comp family, report:

- Representative 7/8/9-level hero lists from best-ranked samples.
- Main carry and alternate carry.
- Main carry minimum star recommendation, median top4 stars, and three-item coverage.
- Key carry equipment: mark only sufficiently supported items as required; otherwise label as high-value or observation.
- Core bond tiers and common sub-bonds.
- Average rank, top4 rate, win rate, sample size, confidence.

## Formation Difficulty And Popularity

Difficulty combines:

- Unfinished pressure: similar low-star or low-equipment versions in bottom four.
- Contest pressure: average number of similar boards in the same match.
- Key-unit pressure: missing or under-starred main carry in poor results.
- Equipment pressure: average carry equipment completeness.

Popularity combines:

- Player share among filtered records.
- Match share where at least one similar board appears.
- Average contest count per match.

Use labels: low, medium, high. Explain when a comp is strong but hard to complete or popular enough to be contested.

## Card Analysis

Primary card ranking:

- single-card appearances, avg rank, top4 rate, win rate.
- adjusted rank with global baseline and a small prior to reduce low-sample noise.
- first-card rankings use `cards[0]`.

Secondary card views:

- per-comp card performance when sample size is sufficient.
- two-card and three-card combinations only as observation rows.
- first-card duo synergy compares both teammates' first cards.
- Team rank is recomputed per match: each team takes its best individual rank, then all teams are sorted to produce team rank 1-4.
- Duo card contribution includes holder avg rank, recomputed team avg rank, lift versus team baseline, and lift versus holder rank after scaling individual rank to team-rank range.
- teammate pair/card synergy only as low-confidence observations unless sample size is strong.

## Strong Heroes And Equipment

Strong heroes should prioritize heroes that are repeatedly identified as main carry or secondary carry.

For each hero:

- carry appearances, avg rank, top4 rate, win rate.
- common equipment, normalized by selected/non-selected name.
- selected-rate and selected-priority.
- required/high-value equipment notes when with-item performance clearly beats without-item performance.
- common 3-item sets when enough complete samples exist.

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
## 主流强势阵容推荐
## 阵容成型难度与热门程度
## 卡牌强度分析
## 强势棋子与装备推荐
## 羁绊表现与啾啾影响
## 版本陷阱分析
## 平衡性调整追踪
## 数据质量与可信度说明
```

## Xiaohongshu Template

Also write `data/latest_meta_analysis_xhs.md`:

- Short title with a clear conclusion and number.
- Top 3 comps with carry, star requirement, key equipment, and compact 8/9-level lineup.
- Per-comp card picks and first-card duo observations.
- Strong heroes and equipment sorted by hero tier descending.
- Single-card highlights and version traps.
- Use short, conversational paragraphs and avoid long tables.
