# -*- coding: utf-8 -*-
"""Replay meta recommendations at batch T and validate them on batch T+1."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ANALYZER_PATH = (
    ROOT / ".cursor" / "skills" / "dzppq-meta-analysis" / "scripts" / "analyze_latest_meta.py"
)
SPEC = importlib.util.spec_from_file_location("dzppq_backtest_analyzer", ANALYZER_PATH)
ANALYZER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ANALYZER
assert SPEC.loader is not None
SPEC.loader.exec_module(ANALYZER)


def build_replay_splits(
    features: list[Any],
    *,
    min_train_batches: int = 2,
    max_splits: int | None = None,
) -> list[dict[str, Any]]:
    """Create strictly causal T -> T+1 splits."""
    batches = ANALYZER.ordered_batches(features)
    splits = []
    for validation_index in range(min_train_batches, len(batches)):
        train_batches = batches[:validation_index]
        validation_batch = batches[validation_index]
        splits.append(
            {
                "cutoff_batch": train_batches[-1],
                "validation_batch": validation_batch,
                "train_batches": train_batches,
                "train": [
                    feature for feature in features if feature.match_batch in set(train_batches)
                ],
                "validation": [
                    feature for feature in features if feature.match_batch == validation_batch
                ],
            }
        )
    if max_splits is not None:
        splits = splits[-max(0, max_splits) :]
    for split in splits:
        # This is intentionally an executable invariant rather than merely a
        # methodology note: no validation or later batch may enter training.
        cutoff = split["cutoff_batch"]
        assert all(
            ANALYZER.batch_ordinal(feature.match_batch)
            <= ANALYZER.batch_ordinal(cutoff)
            for feature in split["train"]
        )
        assert all(
            ANALYZER.batch_ordinal(feature.match_batch)
            == ANALYZER.batch_ordinal(split["validation_batch"])
            for feature in split["validation"]
        )
    return splits


def build_training_rows(
    features: list[Any],
    *,
    min_comp_apps: int,
    recency: bool,
) -> list[dict[str, Any]]:
    training = copy.deepcopy(features)
    if recency:
        ANALYZER.compute_sample_weights(training)
    else:
        for feature in training:
            feature.sample_weight = 1.0
    stages = ANALYZER.cluster_compositions(training, min_comp_apps)
    rows = ANALYZER.merge_comp_strategies(stages, training)
    ANALYZER.calibrate_composition_confidence(rows, training)
    if recency:
        ANALYZER.attach_composition_trends(rows, training)
    return rows


def select_recommendations(
    rows: list[dict[str, Any]],
    method: str,
    limit: int,
) -> list[dict[str, Any]]:
    if method == "legacy":
        eligible = rows
        sort_key = lambda row: (row["overall_strength_score"], row["label"])
    elif method in {"full_history", "new_recency"}:
        # Isolate ranking/history effects from the formal safety gate.
        eligible = rows
        sort_key = lambda row: (row["recommendation_score"], row["label"])
    else:
        eligible = [
            row
            for row in rows
            if row.get("confidence_evidence", {}).get("recommendation_eligible", False)
        ]
        sort_key = lambda row: (row["recommendation_score"], row["label"])
    selected = []
    for style in ANALYZER.PLAY_STYLES:
        selected.extend(
            sorted(
                (row for row in eligible if row.get("play_style") == style),
                key=sort_key,
            )[:limit]
        )
    return selected


def feature_strategy_similarity(feature: Any, row: dict[str, Any]) -> float:
    if feature.archetype != row.get("archetype"):
        return 0.0
    core = {
        item["hero_name"]
        for item in row.get("core_heroes", [])
        if item.get("share", 0) >= 40
    }
    if not core or not feature.hero_set:
        return 0.0
    return len(core & feature.hero_set) / len(core | feature.hero_set)


def evaluate_recommendations(
    recommendations: list[dict[str, Any]],
    validation: list[Any],
) -> dict[str, Any]:
    baseline_avg = mean(feature.rank for feature in validation) if validation else None
    baseline_top4 = (
        mean(feature.rank <= 4 for feature in validation) * 100.0 if validation else None
    )
    matched = []
    matched_rows = []
    for feature in validation:
        candidates = [
            (feature_strategy_similarity(feature, row), row) for row in recommendations
        ]
        similarity, row = max(candidates, default=(0.0, None), key=lambda item: item[0])
        if row is not None and similarity >= 0.40:
            matched.append(feature)
            matched_rows.append(row)
    recommended_avg = mean(feature.rank for feature in matched) if matched else None
    recommended_top4 = mean(feature.rank <= 4 for feature in matched) * 100.0 if matched else None
    recommended_top2 = mean(feature.rank <= 2 for feature in matched) * 100.0 if matched else None
    recommended_win = mean(feature.rank == 1 for feature in matched) * 100.0 if matched else None
    baseline_top2 = mean(feature.rank <= 2 for feature in validation) * 100.0 if validation else None
    baseline_win = mean(feature.rank == 1 for feature in validation) * 100.0 if validation else None
    outcomes_by_row: dict[int, list[Any]] = {}
    for feature, row in zip(matched, matched_rows):
        outcomes_by_row.setdefault(id(row), []).append(feature)
    evaluated_rows = [
        row for row in recommendations if outcomes_by_row.get(id(row))
    ]
    zero_win_rows = [
        row
        for row in evaluated_rows
        if not any(feature.rank == 1 for feature in outcomes_by_row[id(row)])
    ]
    weak_rows = [
        row
        for row in evaluated_rows
        if (
            mean(feature.rank for feature in outcomes_by_row[id(row)])
            > baseline_avg
            or mean(feature.rank <= 4 for feature in outcomes_by_row[id(row)])
            < (baseline_top4 / 100.0)
        )
    ]
    return {
        "recommendation_count": len(recommendations),
        "validation_samples": len(validation),
        "matched_samples": len(matched),
        "coverage": round(len(matched) / len(validation), 4) if validation else None,
        "recommended_avg_rank": round(recommended_avg, 3) if recommended_avg is not None else None,
        "baseline_avg_rank": round(baseline_avg, 3) if baseline_avg is not None else None,
        "avg_rank_lift": (
            round(baseline_avg - recommended_avg, 3)
            if recommended_avg is not None and baseline_avg is not None
            else None
        ),
        "recommended_top4_rate": (
            round(recommended_top4, 2) if recommended_top4 is not None else None
        ),
        "baseline_top4_rate": round(baseline_top4, 2) if baseline_top4 is not None else None,
        "top4_lift": (
            round(recommended_top4 - baseline_top4, 2)
            if recommended_top4 is not None and baseline_top4 is not None
            else None
        ),
        "recommended_top2_rate": (
            round(recommended_top2, 2) if recommended_top2 is not None else None
        ),
        "baseline_top2_rate": round(baseline_top2, 2) if baseline_top2 is not None else None,
        "recommended_win_rate": (
            round(recommended_win, 2) if recommended_win is not None else None
        ),
        "baseline_win_rate": round(baseline_win, 2) if baseline_win is not None else None,
        "evaluated_recommendations": len(evaluated_rows),
        "zero_win_recommendations": len(zero_win_rows),
        "zero_win_recommendation_rate": (
            round(len(zero_win_rows) / len(evaluated_rows), 4)
            if evaluated_rows
            else None
        ),
        "weak_recommendations": len(weak_rows),
        "weak_recommendation_false_positive_rate": (
            round(len(weak_rows) / len(evaluated_rows), 4)
            if evaluated_rows
            else None
        ),
    }


def recommendation_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {row.get("strategy_id", row["label"]) for row in rows}


def aggregate_method(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def average(key: str) -> float | None:
        values = [row[key] for row in rows if row.get(key) is not None]
        return round(mean(values), 4) if values else None

    return {
        "splits": len(rows),
        "recommended_avg_rank": average("recommended_avg_rank"),
        "avg_rank_lift": average("avg_rank_lift"),
        "top4_lift": average("top4_lift"),
        "recommended_top2_rate": average("recommended_top2_rate"),
        "recommended_win_rate": average("recommended_win_rate"),
        "coverage": average("coverage"),
        "stability": average("stability"),
        "zero_win_recommendation_rate": average("zero_win_recommendation_rate"),
        "weak_recommendation_false_positive_rate": average(
            "weak_recommendation_false_positive_rate"
        ),
    }


def run_backtest(
    features: list[Any],
    *,
    min_train_batches: int,
    max_splits: int | None,
    min_comp_apps: int,
    recommendation_limit: int,
) -> dict[str, Any]:
    splits = build_replay_splits(
        features,
        min_train_batches=min_train_batches,
        max_splits=max_splits,
    )
    method_rows: dict[str, list[dict[str, Any]]] = {
        "legacy": [],
        "full_history": [],
        "new_recency": [],
        "current_quality_gate": [],
        "high_cost_ceiling": [],
    }
    previous_ids: dict[str, set[str]] = {}
    split_output = []
    for split in splits:
        uniform_rows = build_training_rows(
            split["train"],
            min_comp_apps=min_comp_apps,
            recency=False,
        )
        recency_rows = build_training_rows(
            split["train"],
            min_comp_apps=min_comp_apps,
            recency=True,
        )
        methods = {
            "legacy": select_recommendations(
                uniform_rows, "legacy", recommendation_limit
            ),
            "full_history": select_recommendations(
                uniform_rows, "full_history", recommendation_limit
            ),
            "new_recency": select_recommendations(
                recency_rows, "new_recency", recommendation_limit
            ),
            "current_quality_gate": select_recommendations(
                recency_rows, "current_quality_gate", recommendation_limit
            ),
        }
        ceiling_samples = ANALYZER.build_high_cost_ceiling_samples(
            recency_rows, copy.deepcopy(split["train"])
        )
        methods["high_cost_ceiling"] = [
            row
            for row in ceiling_samples
            if row.get("confidence_evidence", {}).get("recommendation_eligible", False)
        ][:recommendation_limit]
        evaluations = {}
        for method, recommendations in methods.items():
            evaluation = evaluate_recommendations(
                recommendations,
                split["validation"],
            )
            current_ids = recommendation_ids(recommendations)
            prior_ids = previous_ids.get(method)
            evaluation["stability"] = (
                round(len(current_ids & prior_ids) / len(current_ids | prior_ids), 4)
                if prior_ids is not None and current_ids | prior_ids
                else None
            )
            previous_ids[method] = current_ids
            method_rows[method].append(evaluation)
            evaluations[method] = evaluation
        split_output.append(
            {
                "cutoff_batch": split["cutoff_batch"],
                "validation_batch": split["validation_batch"],
                "train_batches": split["train_batches"],
                "train_samples": len(split["train"]),
                "validation_samples": len(split["validation"]),
                "causal_validation": {
                    "strictly_prior_train_batches": True,
                    "future_batches_in_train": [],
                },
                "methods": evaluations,
            }
        )
    return {
        "methodology": {
            "split": "only batches <= T train recommendations; exactly T+1 validates",
            "legacy": "uniform history, discovery threshold only, legacy strength ordering",
            "full_history": "uniform history through T, current shrunk-score ordering without formal gate",
            "new_recency": "recency weights through T, current scoring without formal gate",
            "current_quality_gate": "recency weights through T plus formal regular-recommendation gates",
            "high_cost_ceiling": "separate final-board-only high-cost ceiling samples; never regular recommendations",
            "future_leakage": "validation batch is excluded before clustering, weighting, and ranking",
        },
        "splits": split_output,
        "summary": {
            method: aggregate_method(rows) for method, rows in method_rows.items()
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="SQLite match DB path")
    parser.add_argument("--min-train-batches", type=int, default=2)
    parser.add_argument("--max-splits", type=int, default=None)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Limit replay to the latest three validation batches",
    )
    parser.add_argument("--min-comp-apps", type=int, default=5)
    parser.add_argument("--recommendation-limit", type=int, default=4)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON path; defaults to stdout and never overwrites formal reports",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    db_path = ANALYZER.find_latest_db(args.db)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        characters, bonds = ANALYZER.load_game_config()
        bots = ANALYZER.find_bot_player_ids(conn)
        features = ANALYZER.load_player_features(conn, bots, characters, bonds)
    finally:
        conn.close()
    result = run_backtest(
        features,
        min_train_batches=args.min_train_batches,
        max_splits=3 if args.quick and args.max_splits is None else args.max_splits,
        min_comp_apps=args.min_comp_apps,
        recommendation_limit=args.recommendation_limit,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
