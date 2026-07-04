# DZPPQ Data Analysis

蛋仔派对对局截图识别、标注和 SQLite 对局数据库构建工具。维护统一标注文件 `data/match_ground_truth.json`，并可导出完整对局数据库 `data/matches_0701.db`。

## 项目内容

- `src/`：识别和数据结构核心代码
- `scripts/`：标注、模板补全、数据库构建脚本
- `assets/templates/`：英雄、卡牌、装备的图像模板
- `screenshots.*/`：对局截图目录（当前批次示例：`screenshots.0701/`）
- `data/match_ground_truth.json`：完整对局标注源，包含队友关系、英雄、星级、装备和卡牌
- `data/matches_0701.db`：由标注源导出的 SQLite 数据库
- `data/template_candidates/`：模板候选裁剪图和 `candidates.json`

## 环境准备

建议使用 Python 3.10+。核心依赖：

```powershell
pip install opencv-python numpy torch torchvision pillow scikit-learn joblib openpyxl
```

虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install opencv-python numpy torch torchvision pillow scikit-learn joblib openpyxl
```

## 常用流程

三个核心脚本按顺序使用：

1. `label_match_ground_truth.py` — 预测与人工标注
2. `suggest_template_candidates.py` — 模板候选生成与审核（有 unknown / 低分时）
3. `build_match_database.py` — 构建 SQLite 对局库

**默认路径：**

| 参数 | 默认值 |
|------|--------|
| GT 文件 | `data/match_ground_truth.json` |
| 截图目录 | `screenshots.0701/` |
| 数据库 | `data/matches_0701.db` |
| 导入前缀 | `screenshots.0701/` |

---

### 1. 预测与标注 — `label_match_ground_truth.py`

#### 批量预测并写入 GT

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0701 predict --write
```

#### 预测单张（仅预览，不写文件）

```powershell
python scripts/label_match_ground_truth.py predict MuMu-20260703-231400-385.png
```

#### 预测单张并写入 GT

```powershell
python scripts/label_match_ground_truth.py predict MuMu-20260703-231400-385.png --write
```

#### 交互式标注单张

```powershell
python scripts/label_match_ground_truth.py label MuMu-20260703-231400-385.png
```

#### 标注所有未验证截图

```powershell
python scripts/label_match_ground_truth.py label --all
```

#### 强制重新标注（含已验证）

```powershell
python scripts/label_match_ground_truth.py label --all --force
```

---

### 2. 模板候选 — `suggest_template_candidates.py`

当标注中仍存在 `unknown` 或低分匹配时使用。

#### 扫描 GT，生成候选裁剪图

```powershell
python scripts/suggest_template_candidates.py generate
```

#### 交互式审核（通过后会写入 `assets/templates/`）

```powershell
python scripts/suggest_template_candidates.py review
```

#### 审核指定候选

```powershell
python scripts/suggest_template_candidates.py review --id c0001
```

#### 重新审核已拒绝的候选

```powershell
python scripts/suggest_template_candidates.py review --include-rejected
```

审核通过的新模板写入 `assets/templates/heroes/` 或 `assets/templates/cards/`；映射到已有模板的修正会回写 `data/match_ground_truth.json`。

---

### 3. 构建对局数据库 — `build_match_database.py`

#### 从已有 GT 导入数据库

```powershell
python scripts/build_match_database.py --force
```

#### 先批量预测，再导入（一条龙）

```powershell
python scripts/build_match_database.py --predict --force
```

#### 允许对局数少于 PNG 数量

```powershell
python scripts/build_match_database.py --force --allow-partial
```

默认输出 `data/matches_0701.db`，数据库表：

- `matches`：截图级元数据
- `pairs`：每局 4 组队友关系
- `players`：每局 8 名玩家
- `heroes`：玩家阵容、星级、装备数和英雄匹配分
- `hero_equipments`：英雄携带装备明细
- `cards`：玩家卡牌及匹配分

---

### 新截图批次完整流程

以 `screenshots.0703/` 为例：

```powershell
# 1. 批量预测
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0703 predict --write

# 2. 人工校正（可选）
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0703 label --all

# 3. 补模板（有 unknown / 低分时）
python scripts/suggest_template_candidates.py generate --path-prefix screenshots.0703/
python scripts/suggest_template_candidates.py review

# 4. 重新预测 + 建库
python scripts/build_match_database.py --screenshot-dir screenshots.0703 --path-prefix screenshots.0703/ --db data/matches_0703.db --predict --force
```

审核通过新模板后，需重新跑 `build_match_database.py --predict --force` 以应用新模板。
