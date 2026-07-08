# DZPPQ Data Analysis

蛋仔派对对局截图识别、标注和 SQLite 对局数据库构建工具。维护统一标注文件 `data/match_ground_truth.json`，并可导出 SQLite 对局数据库。

## 项目内容

- `src/`：识别和数据结构核心代码
- `scripts/`：采集、标注、模板补全、数据库构建脚本
- `assets/templates/`：英雄、卡牌、装备的图像模板
- `screenshots.MMDD/`：对局截图目录，按批次存放（如 `screenshots.0705/`）
- `data/match_ground_truth.json`：完整对局标注源，包含队友关系、英雄、星级、装备和卡牌
- `data/match_latest.db`：从 GT 全量导入后的统一最新分析库（也可按批次导出 `data/matches_MMDD.db`）
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

## 批次与默认路径

日常命令中的 `MMDD` 表示目标截图批次，例如 `0705` 对应目录 `screenshots.0705/`。采集脚本的 `--date` 使用 `MM-DD` 格式，例如 `07-05` 对应同一批次。

脚本默认使用 `data/match_latest.db` 作为统一最新库；全量重建时：

```powershell
python scripts/build_match_database.py --db data/match_latest.db --force --allow-partial
```

单批次补入时显式传入 `--screenshot-dir` 与 `--path-prefix screenshots.MMDD/`。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| GT 文件 | `data/match_ground_truth.json` | 所有批次共用 |
| 截图目录 | `screenshots.0701/` | 仅 `--predict` 或单批次校验时使用 |
| 数据库 | `data/match_latest.db` | 统一最新库 |
| 导入前缀 | 空（全部批次） | 设 `screenshots.MMDD/` 可只导入某批次 |

核心脚本按顺序使用：

1. `capture_daily_screenshots.py` — ADB 自动采集截图
2. `label_match_ground_truth.py` — 预测与人工标注
3. `suggest_template_candidates.py` — 模板候选生成与审核（有 unknown / 低分时）
4. `build_match_database.py` — 构建或补充 SQLite 对局库

---

## 日常使用场景

以下示例以 `0705` 批次为例，替换为你的 `MMDD` 即可。

### 场景 A：采集当天数据并单批次建库

```powershell
# 0. 采集当天对局，输出 screenshots.MMDD/
python scripts/capture_daily_screenshots.py --connect

# 1. 批量预测写入 GT（--workers 并行加速，默认 1）
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 --workers 4 predict --write

# 2. 人工校正未验证截图（预测预取同样可并行，交互校正仍逐张进行）
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 --workers 4 label --all

# 3. 构建当日 SQLite 库
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/matches_0705.db --predict --force
```

### 场景 B：补采昨天或指定日期数据

```powershell
# 采集 7 月 5 日对局，输出 screenshots.0705/
python scripts/capture_daily_screenshots.py --connect --date 07-05

# 如需显式指定输出目录
python scripts/capture_daily_screenshots.py --connect --date 07-05 --output screenshots.0705
```

补采完成后，按场景 A 的步骤 1–3 做预测、标注和建库。

### 场景 C：预测后发现 unknown / 低分，补模板后重新预测

```powershell
python scripts/suggest_template_candidates.py generate --path-prefix screenshots.0705/
python scripts/suggest_template_candidates.py review
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 --workers 4 predict --write
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/matches_0705.db --predict --force
```

审核通过的新模板写入 `assets/templates/heroes/` 或 `assets/templates/cards/`；映射到已有模板的修正会回写 `data/match_ground_truth.json`。

### 场景 D：把新批次补充入统一最新库

```powershell
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/match_latest.db --predict --force
```

- `--db` 决定写入哪个 SQLite 文件
- `--path-prefix` 决定本次从 GT 中筛选哪个截图批次
- 日常维护统一库时保持 `--db data/match_latest.db`，只改 `--path-prefix` 指向新批次

---

## 0. 自动截图采集 — `capture_daily_screenshots.py`

通过 ADB 自动遍历排行榜玩家，采集双人巅峰对局截图。进入派对回顾后直接扫描全部记录，由 OCR 识别「蛋仔碰碰棋」对局，不再点击类别筛选按钮。默认采集 rank 1–100，PNG 落盘到 `screenshots.MMDD/`（`MMDD` 由 `--date` 或当天日期决定）。

### 常用命令

```powershell
# 全量采集（ADB 已连接时可省略 --connect）
python scripts/capture_daily_screenshots.py --connect

# 指定对局日期（昨天或历史日期）
python scripts/capture_daily_screenshots.py --connect --date 07-05

# 多设备时指定 serial
python scripts/capture_daily_screenshots.py --connect --serial emulator-5554

# 小范围测试
python scripts/capture_daily_screenshots.py --connect --start-rank 1 --end-rank 5 --dry-run

# 断点续跑
python scripts/capture_daily_screenshots.py --connect --skip-players data/capture_skip_players.json --resume

# 重置状态后重跑
python scripts/capture_daily_screenshots.py --connect --skip-players data/capture_skip_players.json --reset-state
```

### 常用参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--date` | 当天 `MM-DD` | 目标对局日期 |
| `--output` | `screenshots.MMDD/` | 输出目录 |
| `--connect` | 关 | 运行前执行 `adb connect` |
| `--serial` | 自动 | 多设备时指定 serial |
| `--start-rank` / `--end-rank` | `1` / `100` | 排行榜范围 |
| `--skip-players` | 无 | 手动跳过 rank 列表 JSON |
| `--resume` / `--reset-state` | 关 | 断点续跑 / 重置状态 |
| `--dry-run` | 关 | 导航和 OCR，不保存 PNG |
| `--debug-save-top-players` | `0` | 保存前 N 名玩家的调试截图到 `debug_players/` |
| `--debug-save-top-matches` | `0` | 保存前 N 名玩家当日去重对局截图到 `runs/<run_id>/debug_matches/`（可与 `--dry-run` 同用） |
| `--verbose` / `--log` | 关 / 自动 | 详细日志 / 自定义日志路径 |

### 产物位置

- `screenshots.MMDD/*.png` — 对局截图
- `screenshots.MMDD/failures/*.png` — rank 处理失败时的现场截图
- `screenshots.MMDD/capture_state.json` — 断点状态
- `screenshots.MMDD/capture_log.json` — 完整日志
- `screenshots.MMDD/latest_capture_log.json` — 最近一次运行日志
- `screenshots.MMDD/runs/<run_id>/capture_log.json` — 单次运行日志

---

## 1. 预测与标注 — `label_match_ground_truth.py`

批量处理目录时会显示单行进度条（如 `Predicting [12/80] 15% ...`），避免每张图重复输出内部阶段日志。进入单张交互式标注前会自动换行。

### 批量预测并写入 GT

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 predict --write
```

### 批量预测（并行）

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 --workers 4 predict --write
```

### 预测单张（仅预览，不写文件）

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 predict MuMu-20260705-161100-934.png
```

### 预测单张并写入 GT

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 predict MuMu-20260705-161100-934.png --write
```

### 交互式标注单张

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 label MuMu-20260705-161100-934.png
```

### 标注所有未验证截图

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 label --all
```

`label --all` 会先并行预取尚未缓存的预测（可用 `--workers`），再逐张进入交互式校正。

### 强制重新标注（含已验证）

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0705 label --all --force
```

### 常用参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--workers` | `1` | 批量 `predict` 与 `label --all` 预测预取阶段的并行数 |
| `--quiet` | 关 | 隐藏单张预测的阶段日志 |
| `--method` | `classifier` | 装备数预测方法（`1nn` 备选） |
| `--device` | 自动 | Torch 设备，如 `cpu` / `cuda` |
| `--rebuild-cache` | 关 | 强制重建装备 embedding 缓存 |
| `--no-templates` | 关 | 标注时不提示保存新模板 |

`label --all` 默认跳过已验证截图；`label --all --force` 才会重新标注已验证截图。

---

## 2. 模板候选 — `suggest_template_candidates.py`

当标注中仍存在 `unknown` 或低分匹配时使用。

### 扫描 GT，生成候选裁剪图

```powershell
python scripts/suggest_template_candidates.py generate --path-prefix screenshots.0705/
```

### 交互式审核（通过后会写入 `assets/templates/`）

```powershell
python scripts/suggest_template_candidates.py review
```

### 审核指定候选

```powershell
python scripts/suggest_template_candidates.py review --id c0001
```

### 重新审核已拒绝的候选

```powershell
python scripts/suggest_template_candidates.py review --include-rejected
```

审核通过后需重新预测对应批次，再入库（见场景 C）。

---

## 2b. 直接裁剪存卡牌模板 — `capture_card_template.py`

从某张截图指定行/列裁剪卡牌图标，写入 `assets/templates/cards/`，**不修改 GT**。支持项目外绝对路径（含中文路径）。

- `--row`：玩家行号，1–8（从上到下）
- `--col`：卡牌列号，1–3（从左到右）
- `--name`：模板文件名，如 `黄·法力专注pro`
- `--overwrite`：覆盖已有同名模板（可选）

### 外部截图路径

```cmd
python scripts\capture_card_template.py "C:\Users\wrlin\Documents\MuMu共享文件夹\Screenshots\MuMu-20260705-161100-934.png" --row 3 --col 2 --name "黄·法力专注pro"
```

### 项目内截图

```cmd
python scripts\capture_card_template.py screenshots.0705\MuMu-20260705-161100-934.png --row 2 --col 1 --name "蓝·新卡名"
```

存完模板后重新预测并入库：

```powershell
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/matches_0705.db --predict --force
```

补入总库时把 `--db` 改为 `data/match_latest.db`。

---

## 3. 构建对局数据库 — `build_match_database.py`

### 从已有 GT 导入（默认批次）

```powershell
python scripts/build_match_database.py --force
```

### 先批量预测，再导入（一条龙）

```powershell
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/matches_0705.db --predict --force
```

### 把新批次补入总库

```powershell
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/match_latest.db --predict --force
```

### 允许对局数少于 PNG 数量

```powershell
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/matches_0705.db --force --allow-partial
```

### 关闭相似对局去重

```powershell
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/matches_0705.db --force --no-dedupe-similar
```

### 调整去重阈值

```powershell
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/matches_0705.db --force --similarity-threshold 0.90 --min-hero-rank 0.85
```

### 参数说明

| 参数 | 默认 | 说明 |
|------|------|------|
| `--predict` | 关 | 导入前先跑 `predict --write` |
| `--force` | 关 | 替换数据库中已有行 |
| `--path-prefix` | `screenshots.0701/` | 从 GT 筛选导入批次 |
| `--allow-partial` | 关 | 不警告对局数与 PNG 数不一致 |
| `--no-dedupe-similar` | 关 | 关闭整局相似去重 |
| `--similarity-threshold` | `0.88` | 相似对局判定阈值 |
| `--min-hero-rank` | `0.82` | 去重时各 rank 英雄阵容最低相似度 |
| `--min-pairs` | `0.99` | 去重时队友关系最低相似度 |

**关于 `--force`：** 主要作用于数据库导入阶段，会替换已有行。与 `--predict` 同用时，脚本调用 `label_match_ground_truth.py predict --write`，但不会强制覆盖 GT 中仍有效缓存的预测；模板变更后需重新 `predict --write`，已验证条目需 `label --force` 才会重预测。

### 数据库表

- `matches`：截图级元数据
- `pairs`：每局 4 组队友关系
- `players`：每局 8 名玩家
- `heroes`：玩家阵容、星级、装备数和英雄匹配分
- `hero_equipments`：英雄携带装备明细
- `cards`：玩家卡牌及匹配分
