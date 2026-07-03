---
name: dzppq-meta-analysis
description: Generate DZPPQ meta and environment analysis reports from the latest match SQLite database. Use when analyzing current meta, strong comps, carries, cards, equipment, traps, balance changes, or when the user mentions 对局db、环境分析、阵容推荐、卡牌强度、成型难度、版本陷阱.
---

# DZPPQ Meta Analysis

## Required Workflow

When the user asks for a DZPPQ meta/environment report:

1. Read this file and [report-spec.md](report-spec.md).
2. Resolve the match database:
   - Use the user-provided DB path when present.
   - Otherwise use the newest `data/matches_*.db`.
   - If no DB exists, tell the user to build/import the latest DB first.
3. Run the built-in analyzer before writing conclusions:

```bash
python .cursor/skills/dzppq-meta-analysis/scripts/analyze_latest_meta.py
```

Use `--db <path>` when the user provides a database. If the user provides balance notes in a file, pass `--balance-notes <path>`.

4. Base the final answer on `data/latest_meta_analysis_report.md` and `data/latest_meta_analysis.json`. When the user asks for Xiaohongshu/social copy, use `data/latest_meta_analysis_xhs.md`.
5. Mention data quality caveats: sample size, unknown labels, excluded bot records, and low-confidence segments.

Do not use older report files as the primary source. Older scripts in `scripts/` and `src/meta_analysis.py` are historical references only unless the user explicitly asks to compare with them.

## Mandatory Data Rules

- Exclude rank 7 and rank 8 players when they are teammates in the same match; treat them as bots.
- Exclude `unknown` heroes, cards, and equipment from reference statistics.
- Read hero cost and bonds from `config_s2.py`.
- A bond item named `X啾啾` adds 1 count to bond `X` only when `X` exists in `dict_bond`.
- Normalize `核选X` and `X` as the same equipment, while keeping selected-rate metrics for upgrade priority.
- Main carry judgment must follow player investment: more equipment, more selected equipment, higher stars, and earlier board slot.
- Card order is preserved by `slot_index`; the first card (`cards[0]`) is the duo-focused card.
- Team rank is recomputed per match: sort teams by their best individual rank to get team rank 1-4.
- Exclude card-granted heroes such as `暴龙虾饺` from lineup level and representative lineup lists.

## Output Expectations

Write concise Chinese conclusions. Include:

- Strong comp recommendations with concrete 7/8/9-level hero lists when data supports them.
- Carry analysis for each recommended comp.
- Card strength, with composition-specific notes when sample size allows.
- First-card duo synergy and contribution observations based on recomputed team rank.
- Formation difficulty and popularity, including contest pressure.
- Strong carry heroes and equipment recommendations, including selected-equipment upgrade priority.
- Main carry star requirement and key equipment dependency notes for recommended comps.
- Version traps: popular but weak heroes, comps, bonds, equipment, or cards.
- Balance-change tracking when the user provides patch notes.
- A concise Xiaohongshu-style version at `data/latest_meta_analysis_xhs.md`.

If a section is low confidence, keep it in the report but label it clearly instead of hiding it.
