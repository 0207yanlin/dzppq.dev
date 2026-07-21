# -*- coding: utf-8 -*-

"""Lightweight contract tests for meta report renderers."""



from __future__ import annotations



import importlib.util

import sys

import unittest

from pathlib import Path





ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location(

    "analyze_latest_meta",

    ROOT / ".cursor/skills/dzppq-meta-analysis/scripts/analyze_latest_meta.py",

)

MODULE = importlib.util.module_from_spec(SPEC)

sys.modules[SPEC.name] = MODULE

assert SPEC.loader is not None

SPEC.loader.exec_module(MODULE)





def base_stats(**overrides) -> dict:

    stats = {

        "appearances": 12,

        "weighted_appearances": 8.5,

        "n_eff": 9.2,

        "avg_rank": 3.42,

        "top4_rate": 58.3,

        "top2_rate": 33.3,

        "win_rate": 12.5,

        "bottom4_rate": 41.7,

    }

    stats.update(overrides)

    return stats





def sample_comp(**overrides) -> dict:

    comp = {

        "label": "美食社收菜 / 美食社-5 / 厨师长+蛋小厨",

        "main_bond": "美食社-5",

        "play_style": "赌狗",

        "confidence": "中",

        "archetype": "美食社收菜",

        "archetype_signals": [

            {

                "type": "美食装备",

                "strength": "强",

                "equipment": {

                    "hero_name": "厨师长",

                    "equipment_name": "美味大餐",

                },

            }

        ],

        "stats": base_stats(),

        "mature_stats": base_stats(appearances=10, avg_rank=3.2, top4_rate=62.0),

        "transition_stats": base_stats(appearances=2, avg_rank=5.5, top4_rate=25.0),

        "stage_inversion_diagnostics": {

            "detected": True,

            "rejected_higher_tier_stages": [

                {

                    "label": "美食社-7 / 厨师长+蛋小厨",

                    "bond": "美食社-7",

                    "inversion_reasons": ["avg_rank_regression"],

                }

            ],

        },

        "confidence_evidence": {

            "recommendation_eligible": True,

            "raw_n": 12,

            "weighted_n": 8.5,

            "n_eff": 9.2,

            "recommendation_criteria": {

                "raw_n": {"value": 12, "minimum": 10, "met": True},

                "weighted_n": {"value": 8.5, "minimum": 5.0, "met": True},

                "n_eff": {"value": 9.2, "minimum": 8.0, "met": True},

                "batch_coverage": {"value": 3, "minimum": 2, "met": True},

                "cluster_purity": {"value": 1.0, "minimum": 0.7, "met": True},

            },

            "recommendation_failure_reasons": [],

        },

        "score_breakdown": {

            "shrunk_avg_rank": 3.55,

            "top4_lower_bound": 0.41,

            "win_lower_bound": 0.05,

            "uncertainty_penalty": 0.0,

            "difficulty_penalty": 0.08,

            "trend_adjustment": -0.08,

        },

        "trend": {

            "label": "上升",

            "mode": "rolling",

            "changes": {

                "pick_rate": 1.2,

                "shrunk_avg_rank": -0.2,

                "shrunk_top4_rate": 6.0,

            },

            "reasons": [],

        },

        "cluster_reason": {

            "avg_pair_hero_jaccard": 0.72,

            "archetype_distribution": [{"archetype": "美食社收菜", "appearances": 12}],

        },

        "merge_reason": {

            "archetype": "美食社收菜",

            "mature_member_count": 10,

            "transition_member_count": 2,

            "ownership_rule": "one_player_one_top_level_strategy",

        },

        "main_carries": [

            {"hero_name": "厨师长", "share": 80.0, "carry_rank": 1, "avg_carry_score": 120.0}

        ],

        "carry_requirements": [],

        "carry_equipment_notes": [],

        "jiujiu_requirements": [],

        "common_bonds": [{"bond": "美食社-5", "share": 83.3}],

        "play_style_breakdown": [{"play_style": "赌狗", "share": 100.0}],

        "difficulty": {

            "label": "中",

            "score": 0.4,

            "avg_three_star_units": 1.2,

            "avg_top4_three_star_units": 2.0,

            "avg_same_match_contest": 0.8,

            "unfinished_bottom_rate": 35.0,

            "contest_basis": "阵容相似",

        },

        "popularity": {"label": "中", "pick_rate": 2.5, "match_rate": 4.0},

        "variants": {

            "7": {

                "heroes": ["厨师长", "蛋小厨"],

                "source": "observed",

                "confidence": "中",

                "bond_note": "美食社-3",

                "sample_count": 4,

            },

            "8": {

                "heroes": ["厨师长", "蛋小厨", "蛋小粉"],

                "source": "observed",

                "confidence": "中",

                "bond_note": "美食社-5",

                "sample_count": 5,

            },

            "9": {

                "heroes": ["厨师长", "蛋小厨", "蛋小粉", "蛋小黑"],

                "source": "derived",

                "confidence": "低",

                "bond_note": "美食社-5",

                "sample_count": 0,

            },

        },

        "recommendation_score": 2.8,

    }

    comp.update(overrides)

    return comp





def sample_ceiling(**overrides) -> dict:

    sample = {

        "label": "高费大成上限 / 高费拼多多 / 音乐社-2 / 歌手+鼓手",

        "source_strategy_label": "高费拼多多 / 音乐社-2 / 歌手+鼓手",

        "play_style": "高费",

        "archetype": "高费拼多多",

        "recommendation_status": "正式高费大成上限",

        "stats": base_stats(top2_rate=41.7, win_rate=16.7),

        "confidence_evidence": {

            "recommendation_eligible": True,

            "raw_n": 12,

            "weighted_n": 8.5,

            "n_eff": 9.2,

            "recommendation_criteria": {

                "raw_n": {"value": 12, "minimum": 10, "met": True},

                "weighted_n": {"value": 8.5, "minimum": 5.0, "met": True},

                "n_eff": {"value": 9.2, "minimum": 8.0, "met": True},

                "batch_coverage": {"value": 3, "minimum": 2, "met": True},

                "cluster_purity": {"value": 1.0, "minimum": 0.7, "met": True},

            },

            "recommendation_failure_reasons": [],

        },

        "ceiling_stage": {

            "kind": "高费大成上限样本",

            "final_board_only": True,

            "interpretation": "仅根据最终盘完成度归纳形态，不代表观测到真实过渡过程",

            "high_investment_conditions": [

                "等级>=9",

                "4/5费至少4张且占阵容>=50%",

                "至少一张关键4/5费两星",

                "主C三件装备完整",

            ],

            "two_star_high_cost_heroes": ["歌手"],

            "high_cost_three_star_nonstandard": False,

            "high_cost_three_star_heroes": [],

            "avg_level": 9.0,

            "avg_four_five_cost_count": 4.5,

            "avg_four_five_cost_share": 0.56,

            "main_carry_equipment_complete_rate": 100.0,

            "representative_final_boards": [
                {
                    "source": "sample",
                    "final_board_only": True,
                    "heroes": ["歌手", "鼓手", "吉他手", "贝斯手", "键盘手", "主唱", "舞者", "DJ", "灯光师"],
                    "level": 9,
                    "appearances": 8,
                    "weighted_appearances": 7.2,
                    "share": 66.7,
                    "avg_rank": 2.5,
                    "top4_rate": 75.0,
                    "win_rate": 25.0,
                    "main_carry": "歌手",
                    "confidence": "中",
                }
            ],

        },

        "representative_final_boards": [
            {
                "source": "sample",
                "final_board_only": True,
                "heroes": ["歌手", "鼓手", "吉他手", "贝斯手", "键盘手", "主唱", "舞者", "DJ", "灯光师"],
                "level": 9,
                "appearances": 8,
                "weighted_appearances": 7.2,
                "share": 66.7,
                "avg_rank": 2.5,
                "top4_rate": 75.0,
                "win_rate": 25.0,
                "main_carry": "歌手",
                "confidence": "中",
            }
        ],

    }

    sample.update(overrides)

    return sample





def sample_data(**overrides) -> dict:

    food_comp = sample_comp()

    pdd_comp = sample_comp(

        label="高费拼多多 / 音乐社-2 / 歌手+鼓手",

        main_bond="音乐社-2",

        play_style="高费",

        archetype="高费拼多多",

        archetype_signals=[

            {

                "type": "高费散羁绊",

                "strength": "强",

                "four_five_cost_count": 4,

                "threshold": 3,

                "stable_traits": [],

                "low_cost_three_star_count": 0,

                "main_carry_tier": 5,

                "main_carry_stars": 2,

            }

        ],

        confidence_evidence={

            "recommendation_eligible": False,

            "raw_n": 8,

            "weighted_n": 3.1,

            "n_eff": 2.4,

            "recommendation_criteria": {

                "raw_n": {"value": 8, "minimum": 10, "met": False},

                "weighted_n": {"value": 3.1, "minimum": 5.0, "met": False},

                "n_eff": {"value": 2.4, "minimum": 8.0, "met": False},

                "batch_coverage": {"value": 1, "minimum": 2, "met": False},

                "cluster_purity": {"value": 1.0, "minimum": 0.7, "met": True},

            },

            "recommendation_failure_reasons": [

                {"criterion": "raw_n", "value": 8, "required": 10},

                {"criterion": "weighted_n", "value": 3.1, "required": 5.0},

            ],

        },

        trend={"label": "insufficient", "mode": "rolling", "reasons": ["任一窗口阵容样本少于判定门槛"]},

    )

    data = {

        "generated_at": "2026-07-13T12:00:00+00:00",

        "data_source": "data/match_latest.db",

        "methodology": {"implementation": "rebuilt skill analyzer"},

        "overview": {

            "quality": {

                "matches": 100,

                "players": 400,

                "heroes": 3000,

                "hero_equipments": 5000,

                "cards": 400,

                "unknown_heroes": 0,

                "unknown_cards": 0,

                "unknown_equipment": 0,

                "card_granted_heroes": 0,

                "seven_eight_bot_matches": 0,

                "bot_player_records_excluded": 0,

            },

            "filtered_players": 320,

            "validation": {

                "missing_config_heroes": [],

                "jiujiu_unmapped": [],

            },

        },

        "rankings": {

            "compositions": [food_comp, pdd_comp],

            "composition_recommendations": {

                "赌狗": [food_comp],

                "高费": [pdd_comp],

            },

            "cards": {

                "single_cards": [],

                "single_cards_by_prefix": {},

                "first_card_rankings": [],

                "first_card_rankings_by_prefix": {},

                "blue_cards_team_rank": [],

                "first_card_duo_synergy": [],

                "duo_card_contribution": [],

                "composition_cards": [],

                "teammate_card_pairs_observation": [],

            },

            "heroes_and_equipment": {

                "heroes": [],

                "bonds": [],

                "jiujiu_bonds": [],

                "carry_equipment_recommendations": [

                    {

                        "hero_name": "厨师长",

                        "detail_slug": MODULE.hero_equipment_detail_slug("厨师长"),

                        "hero_traits": ["美食社"],

                        "hero_stats": {

                            "hero_name": "厨师长",

                            "tier": 4,

                            "carry_appearances": 20,

                            "carry_rate": 55.0,

                            "avg_rank": 3.2,

                            "adjusted_avg_rank": 3.4,

                            "top4_rate": 60.0,

                            "appearances": 40,

                        },

                        "recommended_items": [

                            {

                                "equipment_name": "拳王手套",

                                "equipment_kind": "normal",

                                "appearances": 18,

                                "selected_rate": 40.0,

                                "adjusted_avg_rank": 3.1,

                            }

                        ],

                        "recommended_super_items": [

                            {

                                "equipment_name": "幸运猫猫",

                                "equipment_kind": "super",

                                "appearances": 12,

                                "selected_rate": 10.0,

                                "adjusted_avg_rank": 3.0,

                            }

                        ],

                        "recommended_food_items": [

                            {

                                "equipment_name": "杏仁豆腐",

                                "equipment_kind": "food",

                                "appearances": 8,

                                "selected_rate": 5.0,

                                "adjusted_avg_rank": 3.3,

                            }

                        ],

                        "low_sample_observations": [],

                        "recommended_sets": [],

                        "detail_items": [

                            {

                                "equipment_name": "拳王手套",

                                "equipment_kind": "normal",

                                "appearances": 18,

                                "weighted_appearances": 15.5,

                                "n_eff": 14.2,

                                "avg_rank": 3.05,

                                "top4_rate": 61.1,

                                "top2_rate": 33.3,

                                "win_rate": 16.7,

                                "adjusted_avg_rank": 3.1,

                                "selected_rate": 40.0,

                                "sample_quality": "高样本",

                            },

                            {

                                "equipment_name": "幸运猫猫",

                                "equipment_kind": "super",

                                "appearances": 12,

                                "weighted_appearances": 10.0,

                                "n_eff": 9.5,

                                "avg_rank": 2.9,

                                "top4_rate": 66.7,

                                "top2_rate": 41.7,

                                "win_rate": 25.0,

                                "adjusted_avg_rank": 3.0,

                                "selected_rate": 10.0,

                                "sample_quality": "高样本",

                            },

                        ],

                        "has_equipment_data": True,

                    }

                ],

            },

            "super_equipment": {

                "definition": "超级装备按固定白名单统计",

                "catalog": sorted(MODULE.SUPER_EQUIPMENT_NAMES),

                "rankings": [

                    {

                        "strength_rank": 1,

                        "equipment_name": "幸运猫猫",

                        "appearances": 20,

                        "weighted_appearances": 16.0,

                        "n_eff": 14.0,

                        "adjusted_avg_rank": 3.1,

                        "avg_rank": 3.0,

                        "top4_rate": 60.0,

                        "win_rate": 12.0,

                        "confidence": "高",

                        "sample_quality": "高样本",

                        "note": "",

                        "recommended_wearers": [

                            {"hero_name": "厨师长", "appearances": 10, "share": 50.0}

                        ],

                    }

                ],

            },

            "food_equipment": {

                "definition": "美食社装备含美味/绝味/暗黑前缀及杏仁豆腐/椒盐酥糖/岛好锅",

                "catalog": sorted(MODULE.FOOD_SPECIAL_EQUIPMENT_NAMES),

                "rankings": [

                    {

                        "strength_rank": 1,

                        "equipment_name": "岛好锅",

                        "appearances": 1,

                        "weighted_appearances": 1.0,

                        "n_eff": 1.0,

                        "adjusted_avg_rank": 5.0,

                        "avg_rank": 8.0,

                        "top4_rate": 0.0,

                        "win_rate": 0.0,

                        "confidence": "低",

                        "sample_quality": "低样本观察",

                        "note": "名称样本极少，低置信观察，勿作高置信推荐",

                        "recommended_wearers": [

                            {"hero_name": "炸鸡三宝", "appearances": 1, "share": 100.0}

                        ],

                    }

                ],

            },

            "jiujiu": {"jiujiu_rankings": []},

            "primary_bond_strength": {

                "rows": [

                    {

                        "strength_rank": 1,

                        "bond": "美食社",

                        "category": "美食社",

                        "appearances": 20,

                        "adjusted_avg_rank": 3.2,

                        "avg_rank": 3.1,

                        "top4_rate": 60.0,

                        "bottom4_rate": 40.0,

                        "win_rate": 10.0,

                        "common_activation_summary": "5(12)",

                        "source_distribution": {"food_harvest": 20},

                    }

                ],

                "definition": "test",

                "source_definition": {

                    "study_override": "学习社达到配置第三档（4学习）时独占归类",

                    "food_harvest": "收菜装备或美食社收菜原型",

                    "qualified_bond": "达到配置第二档的事实羁绊",

                    "high_cost_pdd": "无合格事实羁绊的高费拼多多兜底",

                },

                "source_distribution": {"food_harvest": 20},

                "category_distribution": {"美食社": 20},

            },

            "traps": {

                "compositions": [],

                "heroes": [],

                "cards": [],

                "bonds": [],

                "equipment": [],

                "formation_pressure_bonds": [],

            },

            "balance_targets": {},

        },

    }

    data.update(overrides)

    return data





class MetaReportContractTests(unittest.TestCase):

    def test_markdown_renders_archetype_without_observation_zone(self) -> None:

        md = MODULE.render_md(sample_data())

        self.assertIn("美食社收菜", md)

        self.assertIn("归类证据", md)

        self.assertNotIn("阵容观察区", md)

        self.assertNotIn("## 高费大成上限", md)

        self.assertIn("高费拼多多", md)

        self.assertIn("n_eff", md)

        self.assertIn("评分分解", md)

        self.assertIn("一起刷刷刷", md)

        self.assertNotIn("合并为同一排行项", md.replace("不再合并为同一排行项", ""))



    def test_markdown_renders_diagnostics_without_ceiling_sections(self) -> None:

        md = MODULE.render_md(sample_data())

        self.assertNotIn("## 高费大成上限", md)

        self.assertNotIn("## 高费大成上限观察", md)

        self.assertIn("## 赌狗阵容推荐", md)

        self.assertIn("## 高费阵容推荐", md)

        self.assertIn("成熟/过渡倒挂", md)

        self.assertIn("低置信提示", md)

        self.assertIn("高费拼多多", md)



    def test_markdown_renders_primary_bond_audit_fields(self) -> None:

        md = MODULE.render_md(sample_data())

        self.assertIn("4学习独占", md)

        self.assertIn("收菜归美食社", md)

        self.assertIn("普通羁绊第二档门", md)

        self.assertIn("高费拼多多兜底", md)

        self.assertIn("归类来源", md)



    def test_markdown_falls_back_when_optional_fields_missing(self) -> None:

        comp = sample_comp()

        for key in (

            "archetype_signals",

            "confidence_evidence",

            "score_breakdown",

            "trend",

            "cluster_reason",

            "merge_reason",

            "mature_stats",

            "transition_stats",

            "stage_inversion_diagnostics",

        ):

            comp.pop(key, None)

        data = sample_data()

        data["rankings"]["composition_recommendations"] = {"赌狗": [comp], "高费": []}

        md = MODULE.render_md(data)

        self.assertIn("美食社收菜", md)

        self.assertNotIn("Traceback", md)



    def test_html_renders_style_filters_without_zone_archetype(self) -> None:

        html = MODULE.render_interactive_html(sample_data())

        self.assertIn('data-style="赌狗"', html)

        self.assertIn('data-style="高费"', html)

        self.assertIn("style-filter", html)

        self.assertNotIn("archetype-filter", html)

        self.assertNotIn("zone-filter", html)

        self.assertNotIn('data-zone=', html)

        self.assertNotIn("观察区", html)

        self.assertNotIn("高费大成上限", html)

        self.assertIn("归类证据", html)

        self.assertIn("评分分解", html)

        self.assertIn("成熟/过渡倒挂", html)

        self.assertIn("低置信提示", html)



    def test_html_primary_bond_panel_includes_audit_fields(self) -> None:

        html = MODULE.render_interactive_html(sample_data())

        self.assertIn("4学习独占", html)

        self.assertIn("收菜归美食社", html)

        self.assertIn("普通羁绊第二档门", html)

        self.assertIn("高费拼多多兜底", html)

        self.assertIn("归类来源", html)



    def test_html_sticky_headers_stay_at_table_top(self) -> None:

        css = MODULE.interactive_dashboard_css()

        self.assertIn(
            "th {\n      position: sticky;\n      top: 0;",
            css,
        )

        self.assertNotIn("top: 64px", css)



    def test_html_primary_bond_header_and_row_column_counts_match(self) -> None:

        import re

        panel = MODULE.render_primary_bond_strength_table_panel(
            sample_data(), panel_id="panel-primary-bond"
        )

        expected_headers = [
            "强度排名",
            "主羁绊",
            "归类",
            "样本",
            "修正名次",
            "平均名次",
            "前四率",
            "后四率",
            "吃鸡率",
            "常见激活数量",
            "常见激活档位",
            "归类来源",
        ]

        headers = re.findall(r"<th\b[^>]*>(.*?)</th>", panel, flags=re.S)

        self.assertEqual(headers, expected_headers)

        body_rows = re.findall(r"<tbody>(.*?)</tbody>", panel, flags=re.S)

        self.assertEqual(len(body_rows), 1)

        data_rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", body_rows[0], flags=re.S)

        self.assertGreaterEqual(len(data_rows), 1)

        for row_html in data_rows:

            cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, flags=re.S)

            self.assertEqual(len(cells), len(expected_headers))



    def test_html_equipment_filters_keep_all_neutral_css(self) -> None:

        css = MODULE.interactive_dashboard_css()

        self.assertIn(':root { color-scheme: dark; }', css)

        self.assertIn('.filter-btn.active:not([data-tier="all"])', css)

        self.assertIn('.trait-filter.active:not([data-trait="all"])', css)

        self.assertIn('.filter-btn.active[data-tier="all"]', css)

        self.assertIn('.trait-filter.active[data-trait="all"]', css)

        self.assertNotIn(
            '.filter-btn.active, .trait-filter.active, .style-filter.active {',
            css,
        )

        self.assertIn('.sort-status {', css)

        self.assertIn('font-size: 14px;', css)

        self.assertIn('background: rgba(15,23,42,.55);', css)



    def test_html_equipment_and_jiujiu_share_dark_table_surfaces(self) -> None:

        data = sample_data()

        html = MODULE.render_interactive_html(data)

        css = MODULE.interactive_dashboard_css()

        self.assertIn('data-hash="equipment"', html)

        self.assertIn('data-hash="jiujiu-wearers"', html)

        self.assertIn("佩戴啾啾棋子推荐", html)

        self.assertIn("显示全部", html)

        self.assertIn('color-scheme: dark', css)

        self.assertIn('th.sort-asc, th.sort-desc { color: #fde68a; }', css)

        self.assertIn('td { color: #cbd5e1;', css)



    def test_html_special_equipment_panels_and_hero_columns(self) -> None:

        data = sample_data()

        html = MODULE.render_interactive_html(data)

        self.assertIn('data-hash="super-equipment"', html)

        self.assertIn('data-hash="food-equipment"', html)

        self.assertIn("超级装备强度排行", html)

        self.assertIn("美食社装备强度排行", html)

        self.assertIn("幸运猫猫", html)

        self.assertIn("岛好锅", html)

        self.assertIn("低置信", html)

        self.assertIn("超级装备", html)

        self.assertIn("美食社装备", html)

        # Normal column should keep normal gear; special gear still appears in its own columns.
        equipment_panel = MODULE.render_equipment_recommendations_panel(
            data, panel_id="panel-equipment"
        )
        self.assertIn("拳王手套", equipment_panel)
        self.assertIn("幸运猫猫", equipment_panel)
        self.assertIn("杏仁豆腐", equipment_panel)



    def test_markdown_special_equipment_sections_and_anchors(self) -> None:

        data = sample_data()

        data["outputs"] = {

            "equipment_xlsx": "data/latest_meta_analysis_equipment.xlsx",

            "interactive_html": "data/环境分析详情.html",

        }

        md = MODULE.render_md(data)

        self.assertIn("### 超级装备强度", md)

        self.assertIn("### 美食社装备强度", md)

        self.assertIn("#super-equipment", md)

        self.assertIn("#food-equipment", md)

        self.assertIn("岛好锅", md)

        self.assertIn("低置信", md)



    def test_blue_card_note_does_not_claim_merged_sss_stats(self) -> None:

        note = MODULE.CARD_MERGE_NOTES["蓝"]

        self.assertIn("分别统计", note)

        self.assertNotIn("一起刷刷刷+天降啾啾pro", note)



    def test_yellow_card_note_describes_jsb_xj_equipment_rules(self) -> None:

        note = MODULE.CARD_MERGE_NOTES["黄"]

        self.assertIn("巨神兵", note)

        self.assertIn("迅迅迅捷双剑", note)

        self.assertIn("数量占优", note)

        self.assertIn("固定种子", note)

        self.assertIn("分别统计", note)

        self.assertEqual(
            MODULE.MERGED_TEMPLATE_EXPANSIONS["黄·巨神兵+迅迅迅捷双剑"],
            ["黄·巨神兵", "黄·迅迅迅捷双剑"],
        )

        md = MODULE.render_md(sample_data())

        self.assertIn("巨神兵之斧", md)

        self.assertIn("固定种子可复现分配", md)



    def test_composition_recommendations_keys_remain_compatible(self) -> None:

        eligible = sample_comp()

        ineligible = sample_comp(

            play_style="高费",

            archetype="高费拼多多",

            confidence_evidence={

                "recommendation_eligible": False,

                "raw_n": 4,

                "weighted_n": 2.0,

                "n_eff": 1.5,

                "recommendation_criteria": {},

                "recommendation_failure_reasons": [{"criterion": "raw_n", "value": 4, "required": 10}],

            },

        )

        recommendations = MODULE.build_composition_recommendations([eligible, ineligible])

        self.assertEqual(set(recommendations), {"赌狗", "高费"})

        self.assertEqual(len(recommendations["赌狗"]), 1)

        self.assertEqual(len(recommendations["高费"]), 1)

        self.assertFalse(
            recommendations["高费"][0]["confidence_evidence"]["recommendation_eligible"]
        )



    def test_html_comp_pages_include_hero_chips(self) -> None:

        html = MODULE.render_interactive_html(sample_data())

        self.assertIn("7 / 8 / 9 级推荐阵容", html)

        self.assertIn("厨师长", html)

        self.assertIn('class="hero-chip"', html)



    def test_html_equipment_hero_detail_links_open_new_tab(self) -> None:

        data = sample_data()

        html = MODULE.render_interactive_html(data)

        slug = MODULE.hero_equipment_detail_slug("厨师长")

        href = MODULE.hero_equipment_detail_href_from_dashboard("厨师长", slug=slug)

        self.assertIn(f'href="{href}"', html)

        self.assertIn('target="_blank"', html)

        self.assertIn('rel="noopener noreferrer"', html)

        self.assertNotIn(f'id="equipment-hero-{slug}"', html)

        self.assertNotIn("openHeroEquipmentDetail", html)

        self.assertNotIn("hero-equipment-details", html)

        self.assertIn("拳王手套", html)



    def test_markdown_equipment_hero_deep_links(self) -> None:

        data = sample_data()

        data["outputs"] = {

            "equipment_xlsx": "data/latest_meta_analysis_equipment.xlsx",

            "interactive_html": "data/环境分析详情.html",

            "hero_equipment_dir": "data/hero-equipment",

        }

        md = MODULE.render_md(data)

        detail_path = MODULE.hero_equipment_detail_relpath("厨师长")

        self.assertIn(detail_path, md)

        self.assertNotIn("#equipment/", md)

        self.assertIn("raw 样本 >10", md)

        self.assertIn("新标签页", md)



    def test_hero_equipment_detail_items_threshold(self) -> None:

        features = []

        for idx in range(12):

            unit = MODULE.Hero(

                id=idx + 1,

                name="厨师长",

                canonical_name="厨师长",

                slot_index=0,

                tier=4,

                stars=2,

                equipment_count=1,

                equipments=[MODULE.Equipment("拳王手套", "拳王手套", False)],

                traits=["美食社"],

                carry_score=100.0,

            )

            member = MODULE.PlayerFeature(

                player_id=idx + 1,

                match_id=idx + 1,

                rank=2 if idx < 8 else 5,

                row_index=idx + 1,

                partner_player=None,

                heroes=[unit],

                cards=[],

                trait_counts=MODULE.Counter(),

                jiujiu_bonus=MODULE.Counter(),

                trait_totals=MODULE.Counter({"美食社": 1}),

                active_traits={"美食社": 2},

                main_bond="美食社",

                main_carry=unit,

                secondary_carry=None,

                hero_set={"厨师长"},

                level=7,

                carry_candidates=[unit],

                sample_weight=1.0,

            )

            features.append(member)

        for idx in range(10):

            features[idx].heroes[0].equipments.append(

                MODULE.Equipment("低样本剑", "低样本剑", False)

            )

            features[idx].heroes[0].equipment_count = 2

        result = MODULE.analyze_heroes_and_equipment(features, min_apps=5, baseline=4.5)

        rec = next(

            row

            for row in result["carry_equipment_recommendations"]

            if row["hero_name"] == "厨师长"

        )

        detail_names = {item["equipment_name"] for item in rec["detail_items"]}

        self.assertIn("拳王手套", detail_names)

        self.assertNotIn("低样本剑", detail_names)

        glove = next(item for item in rec["detail_items"] if item["equipment_name"] == "拳王手套")

        self.assertGreaterEqual(glove["appearances"], 11)

        self.assertIn("weighted_appearances", glove)

        self.assertIn("n_eff", glove)

        self.assertIn("top4_rate", glove)

        self.assertEqual(rec["detail_slug"], MODULE.hero_equipment_detail_slug("厨师长"))





    def test_write_outputs_emits_hero_equipment_pages(self) -> None:
        import tempfile
        from pathlib import Path

        data = sample_data()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            json_path = root / "out.json"
            md_path = root / "out.md"
            html_path = root / "环境分析详情.html"
            xlsx_path = root / "out.xlsx"
            hero_dir = root / "hero-equipment"
            # Avoid xlsx dependency in this contract.
            original_xlsx = MODULE.render_xlsx
            MODULE.render_xlsx = lambda *_args, **_kwargs: None
            try:
                MODULE.write_outputs(data, json_path, md_path, html_path, xlsx_path, hero_dir)
            finally:
                MODULE.render_xlsx = original_xlsx
            page = hero_dir / MODULE.hero_equipment_detail_filename("厨师长")
            self.assertTrue(page.exists())
            page_html = page.read_text(encoding="utf-8")
            self.assertIn("厨师长", page_html)
            self.assertIn("加权平均名次", page_html)
            self.assertIn("拳王手套", page_html)
            self.assertIn("15.5", page_html)
            self.assertIn("../环境分析详情.html#equipment", page_html)
            self.assertIn("hero_equipment_pages", data["outputs"])
            self.assertTrue(data["outputs"]["hero_equipment_pages"])




if __name__ == "__main__":

    unittest.main()

