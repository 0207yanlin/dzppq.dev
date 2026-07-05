# DZPPQ Meta Analyzer Intro

This skill is driven by one production analyzer:

`scripts/analyze_latest_meta.py`

Older files under repository `scripts/` and `src/meta_analysis.py` are historical references only. Use them for comparison, not as the active report pipeline.

## Data Flow

```text
data/matches_*.db
  -> find_latest_db() / find_bot_player_ids()
  -> load_player_features()
  -> cluster_compositions()
  -> merge_comp_strategies()
  -> analyze_heroes_and_equipment() / analyze_cards() / analyze_jiujiu()
  -> find_traps()
  -> build_analysis()
  -> render_md() / render_html() / render_xlsx() / JSON dump
```

Default outputs:

- `data/latest_meta_analysis.json`
- `data/latest_meta_analysis_report.md`
- `data/latest_meta_analysis_report.html`
- `data/latest_meta_analysis_equipment.xlsx`
- `data/latest_meta_analysis_compositions.html`
- `data/latest_meta_analysis_jiujiu_comps.html`
- `data/latest_meta_analysis_jiujiu_wearers.html`
- `data/latest_meta_analysis_equipment.html`
- `data/latest_meta_analysis_trap_compositions.html`
- sortable card/duo/low-cost HTML tables under `data/latest_meta_analysis_*.html`

Excel export requires `openpyxl`.

## Core Objects

- `Hero`: one board unit with normalized name, cost tier, stars, equipment, traits, and carry score.
- `PlayerFeature`: one filtered player board with heroes, cards, active traits, level label, top 3 carry candidates, and team rank.
- `RankStats`: shared avg rank, top4, win-rate accumulator.

Main carry is investment-based:

```text
equipment_count*30 + selected_equipment_count*12 + stars*10 + tier*2 + max(0, 8-slot_index)*1.5
```

## Composition Pipeline

- `load_player_features()` filters bots and unknown entities, computes carry scores, active traits, and board features.
- `classify_play_style()` assigns each player board to `赌狗` or `高费`.
- `cluster_compositions()` groups similar boards into stage-level families.
- `build_composition_row()` builds statistics, variants, carry requirements, jiujiu requirements, difficulty, popularity, strength rank inputs, `play_style`, and `play_style_breakdown`.
- `merge_comp_strategies()` merges duplicate stage rows into strategy-level rows with `mature_stage`, `transition_stages`, and `strength_rank`.
- `enrich_three_star_contest()` adds cross-strategy contest pressure when strategies need the same 3-star main carry, plus low-cost 3-star carry difficulty rows.
- `build_composition_recommendations()` splits strategy recommendations into `赌狗` and `高费`.

## Play Style Rules

- `level <= 6`: always `赌狗`.
- `level >= 8` with no 1/2/3-cost 3-star unit: `高费`.
- `level == 7`: low-cost main carry means `赌狗`; otherwise `高费`.
- Remaining edge cases: low-cost 3-star main carry means `赌狗`; otherwise `高费`.

The analyzer stores strategy-level majority classification plus a breakdown, so mixed strategies remain auditable.

## Rendering

- `render_md()` writes the full audit report. The recommendation area is split into `赌狗阵容推荐` and `高费阵容推荐`. Per-hero equipment tables are not embedded; they go to Excel.
- `render_md()` also includes low-cost 3-star carry difficulty, blue-card team-rank view, jiujiu wearer recommendations, and duo composition synergy when enough samples exist.
- `render_html()` writes a concise 1080px poster. It uses the same split recommendations, not a Markdown-to-HTML conversion, and keeps only compact card/jiujiu/duo highlights.
- `render_table_html_outputs()` writes sortable/filterable HTML tables and paginated comp/trap detail pages.
- `render_xlsx()` writes per-hero equipment, comp carry equipment, common 3-item sets, and low-sample observations.
- HTML should stay visual and compact for the poster; dedicated HTML pages carry full comp, jiujiu, equipment, and trap detail.

## Common Edit Areas

- Play-style rules: `classify_play_style()` and `play_style_summary()`.
- Recommendation split: `build_composition_recommendations()` and the Markdown/HTML renderers.
- Carry requirements: `summarize_carry_requirements()`, `summarize_comp_carry_equipment()`, and `analyze_comp_jiujiu_dependency()`.
- Strength ranking: `overall_strength_score()` and `merge_comp_strategies()`.
- Cross-strategy contest and low-cost 3-star difficulty: `enrich_three_star_contest()`.
- Excel export: `render_xlsx()` and `write_outputs()`.
- Card logic: `analyze_cards()` with prefix-type grouping via `card_prefix_type()` and `aggregate_key_stats_by_prefix()`.
- Jiujiu logic: `analyze_jiujiu()`.
- Duo composition synergy: `analyze_duo_composition_synergy()`.
- Trap logic and mature-strategy-covered lower-tier bonds: `find_traps()`.
- Poster layout: `render_html()` and its small HTML helper functions.

## Safe Change Notes

- Keep bot filtering, unknown filtering, card-granted hero exclusion, and jiujiu bond rules consistent with `report-spec.md`.
- Do not recommend 3-star 4/5-cost carries as a normal requirement; keep them as ceiling samples.
- Keep JSON additive when possible so downstream readers can continue using `rankings.compositions`.
- Re-run the analyzer after changes and inspect all four default outputs.
