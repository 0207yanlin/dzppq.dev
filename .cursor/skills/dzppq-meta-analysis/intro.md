# DZPPQ Meta Analyzer Intro

This skill is driven by one production analyzer:

`scripts/analyze_latest_meta.py`

Older files under repository `scripts/` and `src/meta_analysis.py` are historical references only. Use them for comparison, not as the active report pipeline.

## Data Flow

```text
data/match_latest.db
  -> find_latest_db() / find_bot_player_ids()
  -> load_player_features() + batch recency weights from matches.path
  -> cluster_compositions()
  -> merge_comp_strategies()
  -> analyze_heroes_and_equipment() / analyze_special_equipment() / analyze_cards() / analyze_jiujiu()
  -> find_traps()
  -> build_analysis()
  -> render_md() / render_interactive_html() / write_hero_equipment_pages() / render_xlsx() / JSON dump
```

Default outputs:

- `data/latest_meta_analysis.json`
- `data/latest_meta_analysis_report.md`
- `data/latest_meta_analysis_equipment.xlsx`
- `data/环境分析详情.html`
- `data/hero-equipment/*.html` (one standalone page per hero with equipment detail)

Excel export requires `openpyxl`.

## Core Objects

- `Hero`: one board unit with normalized name, cost tier, stars, equipment, traits, and carry score.
- `PlayerFeature`: one filtered player board with heroes, cards, active traits, level label, top 3 carry candidates, team rank, batch date, and recency weight.
- `RankStats`: shared avg rank, top4, win-rate accumulator with optional batch weighting.

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
- `build_composition_recommendations()` splits strategies into only `赌狗` and `高费`, with no per-style count limit and no observation/ceiling recommendation keys.
- `classify_archetype()` records `archetype`, `archetype_signals`, and high-cost structure; food harvest equipment (`美味`/`绝味`/`暗黑` prefixes) maps to `美食社收菜`. `高费拼多多` additionally requires no low-cost 3-star and a 2-star+ 4/5-cost main carry.
- `merge_comp_strategies()` outputs `mature_stats`, `transition_stats`, `merge_reason`, uses mature strength for recommendations, and sets final `play_style` from the mature stage plus the low-cost 3-star carry gate.
- `build_confidence_evidence()` / `score_composition()` expose recommendation eligibility, failure reasons, shrunk metrics, `n_eff`, and score breakdown as audit/display fields.
- `select_mature_stage()` emits `stage_inversion_diagnostics` when a higher-tier stage is rejected for worse performance.
- High-cost 3-star dependency caps normal star advice at 2 stars and adds risk notes; do not emit separate ceiling recommendation sections.
- `analyze_heroes_and_equipment()` exports truncated recommendation summaries plus untruncated `detail_items` (`appearances > 10`) for standalone HTML pages.
- `primary_bond_business_selections()` / `analyze_primary_bond_strength()` classify food harvest, second-threshold bonds, and high-cost PDD fallback with `source` / `category` audit fields.
- `attach_composition_trends()` adds rolling or balance-boundary trend windows per comp.

## Play Style Rules

- Any lineup 1/2/3-cost 3-star unit: always `赌狗`.
- `level <= 6`: always `赌狗`.
- `level == 7` with a low-cost main carry: `赌狗`.
- Otherwise, boards without low-cost 3-stars are `高费`.

## Archetype Rules

Judged by `classify_archetype()` in order:

1. Food-harvest equipment (`美味` / `绝味` / `暗黑`) -> `美食社收菜`
2. Else if `play_style == 高费`, no low-cost 3-star, enough 4/5-cost units, no stable deep trait, and main carry is 4/5-cost at 2+ stars -> `高费拼多多`
3. Else if a dominant stable trait exists -> `羁绊运营:{trait}`
4. Else -> `拼多多`

Strategy recommendation buckets follow mature-stage `play_style`, not aggregate majority across transition boards. `play_style_breakdown` remains for audit. Mature carry advice that requires a low-cost 3-star still forces `赌狗`.

## Rendering

- `render_md()` writes the full audit report. The recommendation area is split into `赌狗阵容推荐` and `高费阵容推荐` only. Per-hero equipment tables are not embedded; they go to Excel and standalone pages.
- `render_md()` also includes archetype evidence, mature/transition stats, inversion diagnostics, low-confidence notes, trend, confidence evidence, and score breakdown on comp rows when present.
- `render_md()` also includes low-cost 3-star carry difficulty, blue-card team-rank view, jiujiu wearer recommendations, and duo composition synergy when enough samples exist.
- `render_interactive_html()` writes one tabbed dashboard at `data/环境分析详情.html`. It embeds sortable/filterable tables and paginated comp/trap detail panels.
- The composition panel supports only `赌狗/高费` play-style filters plus paging. Archetype and confidence stay in the detail body, not as filter chips.
- Equipment filter buttons keep `全部` neutral via CSS `:not([data-*="all"])`; only concrete tier/trait selections use the golden active style.
- Hero equipment panel shows separate columns for normal / super / food recommendations; clicking a hero name opens `data/hero-equipment/<hero>.html` in a new tab with every single item whose raw appearances exceed 10.
- `#super-equipment` and `#food-equipment` remain independent ranking pages with wearer recommendations.
- `write_hero_equipment_pages()` cleans and regenerates `data/hero-equipment/` on every run.
- `render_xlsx()` writes per-hero equipment, hero super/food recommendations, special equipment rankings, comp carry equipment, common 3-item sets, and low-sample observations.
- The dashboard should stay interactive and compact; use panel hash anchors to link directly to specific panels.

## Common Edit Areas

- Play-style rules: `classify_play_style()`, `play_style_summary()`, and `resolve_strategy_play_style()`.
- Archetype rules: `classify_archetype()` (high-cost PDD gate includes low-cost 3-star and main-carry star/cost checks).
- Recommendation split: `build_composition_recommendations()` and the Markdown/HTML renderers.
- Carry requirements: `summarize_carry_requirements()`, `summarize_comp_carry_equipment()`, and `analyze_comp_jiujiu_dependency()`.
- Strength ranking: `overall_strength_score()` and `merge_comp_strategies()`.
- Cross-strategy contest and low-cost 3-star difficulty: `enrich_three_star_contest()`.
- Excel export: `render_xlsx()` and `write_outputs()`.
- Card logic: `analyze_cards()` with prefix-type grouping via `card_prefix_type()` and `aggregate_key_stats_by_prefix()`. `蓝·一起刷刷刷` / `蓝·天降啾啾pro` are disambiguated by final-board jiujiu count, not merged in rankings. `黄·巨神兵` / `黄·迅迅迅捷双剑` are disambiguated via `resolve_jsb_xj_card_labels()` (axe/sword equipment majority, then seeded clear-sample ratio for ties).
- Jiujiu logic: `analyze_jiujiu()`.
- Super / food equipment ranking: `analyze_special_equipment()`, `is_super_equipment()`, `is_food_equipment()`, `equipment_kind()`.
- Duo composition synergy: `analyze_duo_composition_synergy()`.
- Trap logic and mature-strategy-covered lower-tier bonds: `find_traps()`.
- Interactive dashboard layout: `render_interactive_html()` and panel render helpers.
- Standalone hero equipment pages: `render_hero_equipment_detail_page()` and `write_hero_equipment_pages()`.

## Safe Change Notes

- Keep bot filtering, unknown filtering, card-granted hero exclusion, and jiujiu bond rules consistent with `report-spec.md`.
- Do not recommend 3-star 4/5-cost carries as a normal requirement; keep them as risk notes on high-cost comps.
- Keep JSON additive when possible so downstream readers can continue using `rankings.compositions`.
- Recommendation JSON keys must remain exactly `赌狗` and `高费`.
- Re-run the analyzer after changes and inspect JSON, Markdown, Excel, the unified HTML dashboard, and `data/hero-equipment/`.
