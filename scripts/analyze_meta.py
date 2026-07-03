# -*- coding: utf-8 -*-
"""Generate current-patch meta analysis reports from matches_0701.db."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.meta_analysis import (  # noqa: E402
    build_player_features,
    compute_card_rankings,
    compute_cooccurrence,
    compute_comp_rankings,
    compute_equipment_rankings,
    compute_hero_equipment_recommendations,
    compute_hero_rankings,
    compute_trait_rankings,
    db_overview,
    find_traps,
    load_player_data,
    validate_config,
)

DB_PATH = ROOT / "data" / "matches_0701.db"
JSON_PATH = ROOT / "data" / "meta_analysis.json"
MD_PATH = ROOT / "data" / "meta_analysis_report.md"
TXT_PATH = ROOT / "data" / "composition_analysis.txt"


def run_analysis(conn: sqlite3.Connection) -> dict:
    overview = db_overview(conn)
    validation = validate_config(conn)
    raw_players = load_player_data(conn)
    player_features = build_player_features(raw_players)

    hero_rankings = compute_hero_rankings(conn, min_apps=15)
    weak_heroes = sorted(
        hero_rankings,
        key=lambda row: (-row["avg_rank"], row["top4_rate"]),
    )[:10]
    trait_rankings = compute_trait_rankings(player_features, min_apps=20)
    comp_rankings = compute_comp_rankings(player_features, min_apps=5)
    equipment_rankings = compute_equipment_rankings(conn, min_apps=15)
    equipment_recommendations = compute_hero_equipment_recommendations(
        equipment_rankings["hero_equipment"],
        hero_rankings,
        top_n=3,
    )
    card_rankings = compute_card_rankings(conn, min_apps=20)
    cooccurrence = compute_cooccurrence(conn, min_apps=10)
    traps = find_traps(hero_rankings)

    comp_by_type = {
        row["key"]: row for row in comp_rankings["by_comp_type"]
    }
    recommendations = {
        "赌狗": [
            row
            for row in comp_rankings["by_comp_key"]
            if row["comp_type"] == "赌狗"
        ][:5],
        "八四": [
            row
            for row in comp_rankings["by_comp_key"]
            if row["comp_type"] == "八四"
        ][:5],
        "九五": [
            row
            for row in comp_rankings["by_comp_key"]
            if row["comp_type"] == "九五"
        ][:5],
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": str(DB_PATH.relative_to(ROOT)).replace("\\", "/"),
        "overview": overview,
        "validation": validation,
        "methodology": {
            "rank_definition": "players.rank 越小越强，1 为第一名",
            "exclusions": ["unknown 英雄", "unknown 卡牌", "unknown 装备"],
            "jiujiu_rule": "装备名以「啾啾」结尾时，对应羁绊激活数 +1",
            "comp_type_heuristics": {
                "赌狗": "低费密度高、1-3 费三星多",
                "八四": "4 费核心数量多或 4 费携装核心突出",
                "九五": "5 费棋子密度高",
            },
            "min_samples": {
                "hero": 15,
                "trait_tier": 20,
                "composition": 5,
                "equipment": 15,
                "hero_equipment_pair": 8,
                "card": 20,
                "cooccurrence": 10,
            },
        },
        "rankings": {
            "heroes": hero_rankings,
            "weak_heroes": weak_heroes,
            "traits": trait_rankings,
            "compositions": comp_rankings,
            "composition_recommendations": recommendations,
            "comp_type_summary": comp_by_type,
            "equipment": equipment_rankings,
            "equipment_recommendations": equipment_recommendations,
            "cards": card_rankings,
            "cooccurrence": cooccurrence,
            "trap_picks": traps,
        },
    }


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def _section_lines(title: str, width: int = 72) -> list[str]:
    return [title, "=" * min(len(title), width), ""]


def render_txt_report(data: dict) -> str:
    lines: list[str] = []
    lines.extend(_section_lines("蛋仔派对 S2 当前版本 Meta 分析摘要"))
    lines.append(f"生成时间: {data['generated_at']}")
    lines.append(f"数据源: {data['data_source']}")
    lines.append("")

    summary = data["overview"]["summary"]
    lines.append(
        f"样本: {summary['matches']} 局 / {summary['players']} 玩家 / "
        f"{summary['heroes']} 棋子记录 / {summary['hero_equipments']} 装备记录"
    )
    lines.append(
        f"数据质量: unknown 英雄 {summary['unknown_heroes']} / "
        f"unknown 卡牌 {summary['unknown_cards']}"
    )
    validation = data["validation"]
    if validation["missing_heroes"]:
        lines.append(f"未映射英雄: {', '.join(validation['missing_heroes'])}")
    lines.append("")

    lines.extend(_section_lines("1. 阵容类型推荐"))
    for comp_type in ("赌狗", "八四", "九五"):
        summary_row = data["rankings"]["comp_type_summary"].get(comp_type)
        lines.append(f"[{comp_type}]")
        if summary_row:
            lines.append(
                f"  类型整体: avg_rank={summary_row['avg_rank']:.2f}  "
                f"top4={_fmt_pct(summary_row['top4_rate'])}  n={summary_row['appearances']}"
            )
        picks = data["rankings"]["composition_recommendations"].get(comp_type, [])
        if picks:
            for row in picks[:3]:
                core = " + ".join(row["core_heroes"])
                subs = "、".join(row["recommended_sub_traits"]) or "无"
                lines.append(
                    f"  - {row['main_trait']}-{row['main_tier']} | 核心: {core} | "
                    f"副羁绊: {subs} | avg_rank={row['avg_rank']:.2f} top4={_fmt_pct(row['top4_rate'])} n={row['appearances']}"
                )
        else:
            lines.append("  - 样本不足，暂无稳定推荐")
        lines.append("")

    lines.extend(_section_lines("2. 强势羁绊"))
    for label, key in (
        ("主羁绊档位", "main_trait_tiers"),
        ("副羁绊档位", "sub_trait_tiers"),
    ):
        lines.append(label + ":")
        for row in data["rankings"]["traits"][key][:8]:
            lines.append(
                f"  {row['key']:16s} avg_rank={row['avg_rank']:.2f}  "
                f"top4={_fmt_pct(row['top4_rate'])}  win={_fmt_pct(row['win_rate'])}  n={row['appearances']}"
            )
        lines.append("")

    lines.extend(_section_lines("3. 强势棋子"))
    for row in data["rankings"]["heroes"][:12]:
        lines.append(
            f"  {row['hero_name']:10s} {row['tier']}费  avg_rank={row['avg_rank']:.2f}  "
            f"top4={_fmt_pct(row['top4_rate'])}  pick={_fmt_pct(row['pick_rate'])}  "
            f"avg_stars={row['avg_stars']:.1f}  n={row['appearances']}"
        )
    lines.append("")

    lines.extend(_section_lines("4. 棋子装备推荐"))
    for row in data["rankings"]["equipment_recommendations"][:12]:
        eqs = ", ".join(
            f"{pick['equipment_name']}({pick['avg_rank']:.2f})"
            for pick in row["recommended_equipment"]
        )
        lines.append(f"  {row['hero_name']:10s} -> {eqs}")
    lines.append("")

    lines.extend(_section_lines("5. 其他观察"))
    lines.append("强势共现对:")
    for row in data["rankings"]["cooccurrence"][:8]:
        lines.append(
            f"  {row['pair']:24s} avg_rank={row['avg_rank']:.2f}  "
            f"top4={_fmt_pct(row['top4_rate'])}  n={row['appearances']}"
        )
    lines.append("")
    lines.append("强势卡牌:")
    for row in data["rankings"]["cards"][:8]:
        lines.append(
            f"  {row['card_name']:12s} avg_rank={row['avg_rank']:.2f}  "
            f"top4={_fmt_pct(row['top4_rate'])}  n={row['appearances']}"
        )
    lines.append("")
    lines.append("流行但偏弱（陷阱）:")
    for row in data["rankings"]["trap_picks"][:6]:
        lines.append(
            f"  {row['hero_name']:10s} pick={_fmt_pct(row['pick_rate'])}  "
            f"avg_rank={row['avg_rank']:.2f}  top4={_fmt_pct(row['top4_rate'])}"
        )
    lines.append("")
    return "\n".join(lines)


def render_md_report(data: dict) -> str:
    lines: list[str] = []
    lines.append("# 蛋仔派对 S2 当前版本 Meta 分析报告")
    lines.append("")
    lines.append(f"- 生成时间: `{data['generated_at']}`")
    lines.append(f"- 数据源: `{data['data_source']}`")
    lines.append("")

    summary = data["overview"]["summary"]
    lines.append("## 数据概览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | ---: |")
    for key in ("matches", "players", "heroes", "hero_equipments", "cards"):
        lines.append(f"| {key} | {summary[key]} |")
    lines.append(f"| unknown_heroes | {summary['unknown_heroes']} |")
    lines.append(f"| unknown_cards | {summary['unknown_cards']} |")
    lines.append("")

    lines.append("## 结论摘要")
    lines.append("")
    top_hero = data["rankings"]["heroes"][:3]
    top_trait = data["rankings"]["traits"]["main_trait_tiers"][:3]
    if top_hero:
        hero_text = "、".join(
            f"{row['hero_name']}（avg {row['avg_rank']:.2f}）" for row in top_hero
        )
        lines.append(f"- **强势棋子**：{hero_text}")
    if top_trait:
        trait_text = "、".join(
            f"{row['key']}（avg {row['avg_rank']:.2f}）" for row in top_trait
        )
        lines.append(f"- **强势主羁绊**：{trait_text}")
    for comp_type in ("赌狗", "八四", "九五"):
        picks = data["rankings"]["composition_recommendations"].get(comp_type, [])
        if picks:
            best = picks[0]
            core = " + ".join(best["core_heroes"])
            lines.append(
                f"- **{comp_type}推荐**：{best['main_trait']}-{best['main_tier']}，"
                f"核心 {core}，avg {best['avg_rank']:.2f} / top4 {best['top4_rate']:.1f}%"
            )
    lines.append("")

    lines.append("## 1. 强势阵容与打法推荐")
    lines.append("")
    for comp_type in ("赌狗", "八四", "九五"):
        lines.append(f"### {comp_type}")
        lines.append("")
        summary_row = data["rankings"]["comp_type_summary"].get(comp_type)
        if summary_row:
            lines.append(
                f"该类型整体表现：平均名次 **{summary_row['avg_rank']:.2f}**，"
                f"前四率 **{summary_row['top4_rate']:.1f}%**（n={summary_row['appearances']}）。"
            )
            lines.append("")
        picks = data["rankings"]["composition_recommendations"].get(comp_type, [])
        if not picks:
            lines.append("当前样本下暂无稳定细分阵容。")
            lines.append("")
            continue
        lines.append("| 主羁绊 | 核心棋子 | 推荐副羁绊 | 平均名次 | 前四率 | 样本 |")
        lines.append("| --- | --- | --- | ---: | ---: | ---: |")
        for row in picks[:5]:
            core = " + ".join(row["core_heroes"])
            subs = "、".join(row["recommended_sub_traits"]) or "-"
            lines.append(
                f"| {row['main_trait']}-{row['main_tier']} | {core} | {subs} | "
                f"{row['avg_rank']:.2f} | {row['top4_rate']:.1f}% | {row['appearances']} |"
            )
        lines.append("")

    lines.append("## 2. 强势羁绊推荐")
    lines.append("")
    lines.append("### 主羁绊档位")
    lines.append("")
    lines.append("| 羁绊档位 | 平均名次 | 前四率 | 吃鸡率 | 样本 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for row in data["rankings"]["traits"]["main_trait_tiers"][:12]:
        lines.append(
            f"| {row['key']} | {row['avg_rank']:.2f} | {row['top4_rate']:.1f}% | "
            f"{row['win_rate']:.1f}% | {row['appearances']} |"
        )
    lines.append("")
    lines.append("### 副羁绊档位")
    lines.append("")
    lines.append("| 羁绊档位 | 平均名次 | 前四率 | 样本 |")
    lines.append("| --- | ---: | ---: | ---: |")
    for row in data["rankings"]["traits"]["sub_trait_tiers"][:12]:
        lines.append(
            f"| {row['key']} | {row['avg_rank']:.2f} | {row['top4_rate']:.1f}% | {row['appearances']} |"
        )
    lines.append("")
    lines.append("### 啾啾辅助羁绊")
    lines.append("")
    jiujiu_rows = data["rankings"]["traits"]["jiujiu_assisted_players"]
    if jiujiu_rows:
        lines.append("| 羁绊 | 平均名次 | 前四率 | 样本 |")
        lines.append("| --- | ---: | ---: | ---: |")
        for row in jiujiu_rows[:10]:
            lines.append(
                f"| {row['key']} | {row['avg_rank']:.2f} | {row['top4_rate']:.1f}% | {row['appearances']} |"
            )
    else:
        lines.append("暂无足够样本。")
    lines.append("")

    lines.append("## 3. 强势棋子")
    lines.append("")
    lines.append("| 棋子 | 费用 | 平均名次 | 前四率 | 出场率 | 平均星级 | 样本 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in data["rankings"]["heroes"][:15]:
        lines.append(
            f"| {row['hero_name']} | {row['tier']} | {row['avg_rank']:.2f} | "
            f"{row['top4_rate']:.1f}% | {row['pick_rate']:.1f}% | {row['avg_stars']:.2f} | {row['appearances']} |"
        )
    lines.append("")

    lines.append("## 4. 棋子装备推荐")
    lines.append("")
    lines.append("| 棋子 | 推荐装备 | 说明 |")
    lines.append("| --- | --- | --- |")
    for row in data["rankings"]["equipment_recommendations"][:15]:
        eq_text = " / ".join(
            f"{pick['equipment_name']} (avg {pick['avg_rank']:.2f}, n={pick['appearances']})"
            for pick in row["recommended_equipment"]
        )
        lines.append(f"| {row['hero_name']} | {eq_text} | 基于装备×棋子共现表现 |")
    lines.append("")

    lines.append("## 5. 其他分析")
    lines.append("")
    lines.append("### 棋子共现对")
    lines.append("")
    lines.append("| 组合 | 平均名次 | 前四率 | 样本 |")
    lines.append("| --- | ---: | ---: | ---: |")
    for row in data["rankings"]["cooccurrence"][:10]:
        lines.append(
            f"| {row['pair']} | {row['avg_rank']:.2f} | {row['top4_rate']:.1f}% | {row['appearances']} |"
        )
    lines.append("")
    lines.append("### 卡牌强度")
    lines.append("")
    lines.append("| 卡牌 | 平均名次 | 前四率 | 样本 |")
    lines.append("| --- | ---: | ---: | ---: |")
    for row in data["rankings"]["cards"][:12]:
        lines.append(
            f"| {row['card_name']} | {row['avg_rank']:.2f} | {row['top4_rate']:.1f}% | {row['appearances']} |"
        )
    lines.append("")
    lines.append("### 流行但偏弱")
    lines.append("")
    for row in data["rankings"]["trap_picks"][:8]:
        lines.append(
            f"- **{row['hero_name']}**：出场率 {row['pick_rate']:.1f}%，"
            f"平均名次 {row['avg_rank']:.2f}，前四率 {row['top4_rate']:.1f}%"
        )
    lines.append("")

    lines.append("## 6. 方法说明与限制")
    lines.append("")
    method = data["methodology"]
    lines.append(f"- 排名定义：{method['rank_definition']}")
    lines.append(f"- 排除项：{', '.join(method['exclusions'])}")
    lines.append(f"- 啾啾规则：{method['jiujiu_rule']}")
    lines.append("- 阵容类型采用启发式分类，同一玩家可能接近多种类型，最终取最高分标签。")
    validation = data["validation"]
    if validation["missing_heroes"]:
        lines.append(
            f"- 以下英雄使用了 fallback 映射或未完全映射：{', '.join(validation['missing_heroes'])}"
        )
    if validation["config_heroes_not_in_db"]:
        lines.append(
            f"- 配置中存在但样本未出现的棋子：{', '.join(validation['config_heroes_not_in_db'])}"
        )
    lines.append("- 当前样本来自 91 局对局，部分细分阵容样本量较小，解读时需结合 n 值。")
    lines.append("")
    return "\n".join(lines)


def export_outputs(data: dict) -> None:
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    MD_PATH.write_text(render_md_report(data), encoding="utf-8")
    TXT_PATH.write_text(render_txt_report(data), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run meta analysis pipeline")
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help="SQLite database path",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    try:
        data = run_analysis(conn)
        export_outputs(data)
        print(f"Wrote {JSON_PATH}")
        print(f"Wrote {MD_PATH}")
        print(f"Wrote {TXT_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
