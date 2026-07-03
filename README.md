# DZPPQ Data Analysis

蛋仔派对对局截图识别、标注和 SQLite 对局数据库构建工具。当前仓库围绕 `screenshots.0701/` 的 90 张对局截图，维护一份统一标注文件 `data/match_ground_truth.json`，并可导出完整对局数据库 `data/matches_0701.db`。

## 项目内容

- `src/`：识别和数据结构核心代码。
- `scripts/`：标注、模板补全、数据库构建和校验脚本。
- `assets/templates/`：英雄、卡牌、装备的图像模板。
- `screenshots.0701/`：本次对局数据库使用的截图集合。
- `data/match_ground_truth.json`：完整对局标注源，包含队友关系、英雄、星级、装备和卡牌。
- `data/matches_0701.db`：由标注源导出的 SQLite 数据库。
- `data/matches_0701_report.json`：数据库完整性校验报告。

## 环境准备

建议使用 Python 3.10+。仓库当前没有锁定依赖文件，核心脚本会用到：

```powershell
pip install opencv-python numpy torch torchvision pillow scikit-learn joblib
```

如果使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install opencv-python numpy torch torchvision pillow scikit-learn joblib
```

## 常用流程

### 1. 批量预测或更新标注

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0701 predict --write
```

该命令会读取截图并写入 `data/match_ground_truth.json`。如果需要人工校正单张截图，可使用同一脚本的交互式标注能力。

### 2. 生成并审核新模板候选

当标注中仍存在 `unknown` 或低分匹配时，先生成候选裁剪图：

```powershell
python scripts/suggest_template_candidates.py generate
```

再交互式审核：

```powershell
python scripts/suggest_template_candidates.py review
```

审核通过的新模板会写入 `assets/templates/heroes/` 或 `assets/templates/cards/`；映射到已有模板的修正会回写 `data/match_ground_truth.json`。

### 3. 构建对局数据库

```powershell
python scripts/build_match_database.py --force
```

如需在构建前重新预测：

```powershell
python scripts/build_match_database.py --predict --force
```

默认输出为 `data/matches_0701.db`。数据库表包括：

- `matches`：截图级元数据。
- `pairs`：每局 4 组队友关系。
- `players`：每局 8 名玩家。
- `heroes`：玩家阵容、星级、装备数和英雄匹配分。
- `hero_equipments`：英雄携带装备明细。
- `cards`：玩家卡牌及匹配分。

### 4. 生成 Meta 分析报告

```powershell
python scripts/analyze_meta.py
```

默认读取 `data/matches_0701.db`，输出：

- `data/meta_analysis.json`：结构化分析结果
- `data/meta_analysis_report.md`：详尽中文报告
- `data/composition_analysis.txt`：纯文本摘要

### 5. 校验数据库

```powershell
python scripts/verify_match_database.py
```

当前报告显示：

- `matches`: 91
- `players`: 728
- `heroes`: 5502
- `hero_equipments`: 7860
- `cards`: 2184
- `unknown_heroes`: 13
- `unknown_cards`: 75
- `players_ok` 和 `cards_ok` 均为 `true`

注意：`data/matches_0701_report.json` 中显示 `gt_count=91`、`png_count=90`，多出的标注和数据库记录是 `MuMu-20260701-234356-319.png`。如果这张截图已经不在 `screenshots.0701/`，需要决定是补回截图，还是从 `data/match_ground_truth.json` 中移除对应记录后重新构建数据库。

## 清理建议

可以直接再生成、通常不需要长期保留的文件：

- `data/template_candidates/`：模板候选裁剪图和 `candidates.json`，当前 `pending_count=0`，如果已完成模板审核，可以删除。
- `data/speedup_report.json`、`data/speedup_snapshot.json`：性能优化验证输出，可由 `scripts/_speedup_verification.py` 重新生成。
- `test.ipynb`：临时探索 notebook，若没有需要保留的分析过程，可以删除。

建议先归档或确认后再删的旧流程文件：

- `data/matches.db`、`data/ground_truth.json`：旧版英雄/卡牌数据库和标注文件；当前主流程已切换到 `data/match_ground_truth.json` 和 `data/matches_0701.db`。
- `scripts/extract_matches.py`、`scripts/generate_ground_truth.py`、`src/db.py`：旧版数据库构建流程，只覆盖英雄、星级和卡牌，不包含完整对局信息。
- `scripts/_debug_*.py`、`scripts/_analyze_*.py`、`scripts/_test_*.py`：调试和一次性验证脚本；如果近期不再调识别规则，可以按需删除或移到归档目录。

建议保留的核心文件：

- `data/match_ground_truth.json`
- `data/matches_0701.db`
- `data/equipment_ground_truth.json`
- `data/equipment_classifier.joblib`
- `data/equipment_embeddings.npz`
- `assets/templates/`
- `screenshots.0701/`
- `src/match_ground_truth.py`
- `src/match_db.py`
- `scripts/label_match_ground_truth.py`
- `scripts/build_match_database.py`
- `scripts/analyze_meta.py`
- `scripts/verify_match_database.py`
- `scripts/suggest_template_candidates.py`
