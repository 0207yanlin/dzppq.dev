# DZPPQ Data Analysis

蛋仔派对对局截图识别、标注和 SQLite 对局数据库构建工具。维护统一标注文件 `data/match_ground_truth.json`，并可导出 SQLite 对局数据库。

## 项目内容

- `src/`：识别和数据结构核心代码
- `scripts/`：采集、标注、一键批次处理、模板补全、数据库构建脚本
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
2. `process_match_batch.py` — 截图后一键完成标注、入库与环境分析（推荐日常入口）
3. `suggest_template_candidates.py` — 模板候选生成与审核（有 unknown / 低分时）
4. 底层分步脚本（排查或高级用法）：`label_match_ground_truth.py`、`build_match_database.py`、`analyze_latest_meta.py`

---

## 日常使用场景

以下示例以 `0705` 批次为例，替换为你的 `MMDD` 即可。

### 场景 A：采集当天数据并完成标注 / 入库 / 环境分析

```powershell
# 0. 采集当天对局，输出 screenshots.MMDD/
python scripts/capture_daily_screenshots.py --connect

# 1. 一键：预测未验证截图 -> 补入 data/match_latest.db -> 生成环境分析报告
#    --batch 省略时默认使用今天的 MMDD；预测默认 --workers 4，不进入人工确认
python scripts/process_match_batch.py --batch 0705
```

等价于依次执行预测、入库、环境分析三步；任一步失败会立即停止。自动预测保留
`verified=false`，以便后续与人工审核数据区分，但不会阻止入库和报告生成。

### 场景 B：补采昨天或指定日期数据

```powershell
# 采集 7 月 5 日对局，输出 screenshots.0705/
python scripts/capture_daily_screenshots.py --connect --date 07-05

# 如需显式指定输出目录
python scripts/capture_daily_screenshots.py --connect --date 07-05 --output screenshots.0705
```

补采完成后：

```powershell
python scripts/process_match_batch.py --batch 0705
```

### 场景 C：预测后发现 unknown / 低分，补模板后重新预测

```powershell
python scripts/suggest_template_candidates.py generate --path-prefix screenshots.0705/
python scripts/suggest_template_candidates.py review
python scripts/process_match_batch.py --batch 0705
```

审核通过的新模板写入 `assets/templates/heroes/` 或 `assets/templates/cards/`；映射到已有模板的修正会回写 `data/match_ground_truth.json`。`process_match_batch.py` 会重新预测未验证数据（跳过已验证）、补入统一库并刷新环境分析。

### 场景 D：只把已有 GT 批次补入统一最新库

若标注已完成，只需重建库或刷新报告，可继续用底层命令：

```powershell
python scripts/build_match_database.py --screenshot-dir screenshots.0705 --path-prefix screenshots.0705/ --db data/match_latest.db --force
python .cursor/skills/dzppq-meta-analysis/scripts/analyze_latest_meta.py --db data/match_latest.db
```

- `--db` 决定写入哪个 SQLite 文件
- `--path-prefix` 决定本次从 GT 中筛选哪个截图批次
- 日常维护统一库时保持 `--db data/match_latest.db`，只改 `--path-prefix` 指向新批次

---

## 0b. 一键批次处理 — `process_match_batch.py`

截图采集完成后的推荐入口。固定使用 `data/match_ground_truth.json`，数据库默认写入
`data/match_latest.db`，也可用 `--db` 指向临时库；按批次目录 `screenshots.MMDD/` 依次执行：

1. `label_match_ground_truth.py --workers 4 label --all --no-review`（并行预测并保存为未验证，不进入交互校正）
2. `build_match_database.py --path-prefix screenshots.MMDD/ --db data/match_latest.db --force`（不重复预测）
3. `analyze_latest_meta.py --db data/match_latest.db`

如需人工审核，单独运行不带 `--no-review` 的 `label --all`；审核结果会标记为
`verified=true`，下次一键处理会自动跳过。

### 常用命令

```powershell
# 处理今天批次（MMDD = 当天月日）
python scripts/process_match_batch.py

# 处理指定批次
python scripts/process_match_batch.py --batch 0705

# 调整标注预测并行度
python scripts/process_match_batch.py --batch 0705 --workers 8

# 安全重建到全新临时库
python scripts/process_match_batch.py --batch 0727 --db data/match_0727_rebuild.db

# 只打印将要执行的命令，不真正跑
python scripts/process_match_batch.py --batch 0705 --dry-run
```

### 常用参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--batch` | 今天的 `MMDD` | 对应 `screenshots.MMDD/` |
| `--workers` | `4` | 传给标注脚本的预测预取并行数 |
| `--db` | `data/match_latest.db` | 入库和环境分析使用的 SQLite，可指向临时库 |
| `--dry-run` | 关 | 只打印子命令，不执行 |

默认产物：更新后的 `data/match_ground_truth.json`、`data/match_latest.db`，以及环境分析相关文件（见下方「环境分析报告」一节）。

---

## 0. 自动截图采集 — `capture_daily_screenshots.py`

通过 ADB 自动遍历排行榜玩家，采集双人巅峰对局截图。进入个人信息页后，先对 ROI `(510, 450, 700, 550)` 做 OCR：识别到「派对回顾」则点击 `(600, 500)` 进入派对回顾；未识别到则记为隐藏（`private_party_review`）并返回排行榜，**不会**再点击旧坐标 `(200, 400)`。进入派对回顾后直接扫描全部记录，由 OCR 识别「蛋仔碰碰棋 + 双人巅峰」对局，不再点击类别筛选按钮。默认采集 rank 1–100，PNG 落盘到 `screenshots.MMDD/`（`MMDD` 由 `--date` 或当天日期决定）。

滚动区 ROI 同时完整识别到连续两名玩家时，处理第一名并返回排行榜后不会滑动，而是直接处理第二名；第二名完成后再滑动。只有 OCR 精确识别到下一排名才会连续处理，例如 `[23, 24]` 可直接处理 24，而下一行未完全显示导致结果为 `[23, 4]` 时会先滑动，不会把 4 误当作 24。

### 卡牌详情工作簿与采集

`data/card_details.xlsx` 固定包含 `白`、`蓝`、`黄`、`彩`、`同模板组合` 五个 sheet。四色 sheet 的两列是 `卡牌名称`、`文字详情`；`同模板组合` 的两列是 `模板名称`、`卡牌列表`，其中模板名称填写 `assets/templates/cards/*.jpg` 的文件名 stem（不含 `.jpg`），卡牌列表填写至少两个完整卡名组成的 JSON 数组，例如 `["蓝·候选甲", "黄·候选乙"]`。

手工新增共享模板时，在 `同模板组合` 增加一行，并在各候选颜色 sheet 中增加对应的完整卡名和文字详情；卡名须带 `白·` / `蓝·` / `黄·` / `彩·` 前缀，跨色同名候选尤其需要填写可区分的文字详情。保存工作簿后，下次启动采集脚本时即重新加载并生效。也可初始化或同步工作簿：

```powershell
python scripts/init_card_details_workbook.py
```

默认同步保留手工详情和自定义组合，并补入内置组合。`--force` 会用内置组合替换整个 `同模板组合` sheet，删除自定义组合；仅由这些组合引入的候选详情行也会从重建后的四色 sheet 消失，使用前应备份工作簿。

采集先把卡槽分为 `occupied`、`empty`、`uncertain`：只有 `occupied` 且模板匹配的原始 asset stem 存在于工作簿 `同模板组合` 时，才点击卡牌并 OCR 悬浮详情；`empty` / `uncertain` 均不点击。详情面板中的名称不带颜色前缀；跨色同名先按名称缩小范围，再靠 `文字详情` 唯一确认。确认失败记为 `unknown`，并在 `screenshots.MMDD/card_review/` 保存复核裁剪。

每张 `foo.png` 对应同目录 `foo.cards.json`，记录完整 8×3 卡槽。GT 预测默认优先使用有效 sidecar 中的卡牌结果，再回退离线模板识别；已人工验证的 GT 仍保持不变。详情 OCR 确认的卡牌会以 `source=detail_ocr` 写入 GT 和 SQLite 的 `cards.card_source`，统计、报告和规范化流程会保留其真实名称，不再应用旧的上下文硬编码规则；历史记录的来源为空，继续沿用原规则。需要忽略 sidecar 并强制走离线卡牌识别时：

```powershell
python scripts/label_match_ground_truth.py --screenshot-dir screenshots.0727 predict --ignore-card-sidecar
```

`capture_state.json` 的每个 rank 记录会在 `sidecar_paths` 中保存已写入的 sidecar 路径。采集可通过 `--card-details-workbook PATH` 改用其他工作簿。

### 共享卡牌模板开发

共享模板指“同一个 JPG 图标对应多个游戏内卡名”。它与 `src/detect_cards.py` 的
`VISUAL_CARD_GROUPS` 不同：后者用于多个不同 JPG 之间的形状/颜色消歧，不会触发详情 OCR。

数据流如下：

```text
assets/templates/cards/{stem}.jpg
  + data/card_details.xlsx（同模板组合、四色详情）
  → 模板匹配取得 top1_raw_asset_stem
  → 点击详情并 OCR 唯一卡名
  → *.cards.json（source=detail_ocr）
  → match_ground_truth.json → match_latest.db
  → 环境分析 → 三选一推荐器 → EXE
```

新增共享组合时按以下顺序处理：

1. 确认 `assets/templates/cards/{stem}.jpg` 已存在；`stem` 必须与工作簿“模板名称”完全一致。
2. 在 `data/card_details.xlsx` 的 `同模板组合` 增加 `stem` 和至少两个带颜色前缀的具体卡名。
3. 在 `白` / `蓝` / `黄` / `彩` sheet 为每个具体卡名补充详情；名称相似或跨色同名时，详情必须足以唯一识别。
4. 将仓库内置组合加入 `src/card_catalog.py` 的 `DEFAULT_SAME_TEMPLATE_GROUPS`，再运行
   `python scripts/init_card_details_workbook.py`。该常量用于团队默认、工作簿同步和测试，不能代替正式工作簿。
5. 若 JPG stem 是复合名称，或报告在工作簿缺失时仍需展开候选，同步
   `.cursor/skills/dzppq-meta-analysis/scripts/analyze_latest_meta.py` 的
   `MERGED_TEMPLATE_EXPANSIONS`。
6. 推荐器会从最新 DB（不可用时回退 JSON）自动加载已入库的具体卡名，无需为常规新增卡牌修改
   `src/card_pick_recommend.py` 或 EXE。只有希望尚无任何样本的新卡也能被 OCR 匹配并显示“暂无统计”时，
   才将其预注册到 `LOGICAL_CARD_KEYS`。
7. 不要因为新增卡牌修改
   `scripts/build_card_pick_recommender_exe.py` 的 `REQUIRED_CONCRETE_CARD_KEYS`；
   该集合只是既有发布数据的完整性断言，不是推荐器卡牌目录。只有拆除旧合并统计键时，才按需更新
   `STALE_MERGED_RANKING_KEYS`。
8. 小范围采集并检查 sidecar：共享模板必须输出某个具体卡名和 `source=detail_ocr`；
   无法唯一确认时必须为 `unknown`，不得回退为组合 stem。
9. 运行 `python scripts/process_match_batch.py --batch MMDD`，刷新 GT、数据库和分析产物；
   发布时再执行 `python scripts/build_card_pick_recommender_exe.py --clean`。

各配置并非每次都要修改：

| 配置 | 何时修改 |
|------|----------|
| JPG、工作簿 `同模板组合`、四色详情 | 每个共享组合必改 |
| `DEFAULT_SAME_TEMPLATE_GROUPS` | 仓库正式内置组合必改；纯本地临时组合可不改 |
| `MERGED_TEMPLATE_EXPANSIONS` | 复合 stem、无工作簿报告回退或历史拆分时修改 |
| `CARD_LABEL_ALIASES` | 只用于稳定别名、OCR 变体或历史归一；0727+ 具体卡不应互相合并 |
| `OCR_EXACT_QUERY_ALIASES` | 三选一卡名 OCR 有稳定错字时修改 |
| `LOGICAL_CARD_KEYS` | 仅需让尚未入库的零样本卡也可识别时预注册；已入库新卡无需修改 |
| `REQUIRED_CONCRETE_CARD_KEYS` | 既有发布数据完整性断言；不要随常规新增卡牌修改 |
| `STALE_MERGED_RANKING_KEYS` | 退役旧合并排行键时修改 |
| 0727 backfill / GT normalize 脚本 | 仅清理已入库历史数据时修改 |

sidecar、`data/match_ground_truth.json`、`data/match_latest.db`、元分析报告以及
`dist/` 内数据都是生成物，不应手工逐项改名。历史清理应先改 sidecar/GT 的来源数据，再重建 DB 和报告。

批次以 `screenshots.MMDD/` 路径为准，不以 PNG 文件名中的截图时间为准。通常
`screenshots.0727` 起只保留详情 OCR 的具体卡名；若某个共享模板在启用日尚未采集详情，
应把该批次相关槽位回退为 `unknown`，在文档中注明首个有效批次，并从下一批重新采集。

建议验证：

```powershell
python -c "from src.card_details import validate_card_details_workbook; validate_card_details_workbook(); print('OK')"
python -m pytest scripts/test_card_details.py scripts/test_card_capture_detection.py scripts/test_card_capture_runtime.py -q
python -m pytest scripts/test_detect_cards.py scripts/test_card_sidecar_ground_truth.py -q
python -m pytest scripts/test_meta_report_contracts.py -q -k card
python -m pytest scripts/test_card_pick_recommender.py -q
```

### UI 版本更新校准

游戏改版后若入口位置变化，按以下顺序同步：

1. 在 `test_adb.ipynb` 验证个人信息页 ROI / 点击点（当前：`(510,450,700,550)` → `tap(600,500)`）
2. 将常量 promote 到 `src/adb_capture.py`（`PROFILE_PARTY_REVIEW_ENTRY_BOX`、`TAP_PROFILE_PARTY_REVIEW`）
3. 运行 `python -m pytest scripts/test_capture_daily_screenshots.py -q`
4. 小范围 dry-run：`python scripts/capture_daily_screenshots.py --connect --start-rank 1 --end-rank 5 --dry-run --debug-save-top-players 5 --verbose`

真机启用卡牌详情采集前，还需按设备分辨率 / 缩放校准 `src/layout.py` 中的卡槽 ROI 与点击中心、详情名称 / 文字 ROI，校准 `src/detect_cards.py` 的三态 presence 阈值，并确认点击后详情面板已经稳定显示再截图 OCR。

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
| `--card-details-workbook` | `data/card_details.xlsx` | 卡牌详情与同模板组合工作簿 |
| `--start-rank` / `--end-rank` | `1` / `100` | 排行榜范围 |
| `--skip-players` | 无 | 手动跳过 rank 列表 JSON |
| `--resume` / `--reset-state` | 关 | 断点续跑 / 重置状态 |
| `--dry-run` | 关 | 导航和 OCR，不保存 PNG |
| `--debug-save-top-players` | `0` | 保存前 N 名玩家的调试截图到 `debug_players/` |
| `--debug-save-top-matches` | `0` | 保存前 N 名玩家当日去重对局截图到 `runs/<run_id>/debug_matches/`（可与 `--dry-run` 同用） |
| `--verbose` / `--log` | 关 / 自动 | 详细日志 / 自定义日志路径 |

### 产物位置

- `screenshots.MMDD/*.png` — 对局截图
- `screenshots.MMDD/*.cards.json` — 与 PNG 同 stem 的卡牌 sidecar
- `screenshots.MMDD/card_review/` — 未确认或 uncertain 卡槽的复核裁剪
- `screenshots.MMDD/failures/*.png` — rank 处理失败时的现场截图
- `screenshots.MMDD/capture_state.json` — 断点状态（含 `sidecar_paths`；隐藏派对回顾记为 `skip_reason=private_party_review`）
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
| `--no-review` | 关 | 配合 `label --all` 保存未验证预测并跳过人工确认 |

`label --all` 默认跳过已验证截图；`label --all --force` 才会重新标注已验证截图。

### 卡牌同图标歧义

从 `screenshots.0727` 起，共用图标卡牌由详情 OCR 区分，sidecar、GT、数据库、环境分析和
三选一推荐器都保留具体卡名。0727 以前的阵容/装备推断规则仅用于历史数据，不进入当前统计。
`蓝·半步满级` / `蓝·满级玩家` 的共享模板在 0727 未采集详情，因此该组 0727 槽位统一为
`unknown`，从 `screenshots.0728` 起按两个具体卡名收集和统计。

| 层 | 位置 | 作用 |
|----|------|------|
| 图像级 | `src/detect_cards.py` | 识别共用模板并取得候选卡组 |
| 详情级 | `src/card_capture.py` + `data/card_details.xlsx` | 点击卡牌后按名称/文字详情确认唯一具体卡名 |
| 数据级 | sidecar → GT → SQLite | 0727+ 原样保留详情确认结果，不再合并或按最终阵容猜测 |

`蓝·开攒` / `蓝·大亨`、`蓝·利己主义` / `蓝·最后的波纹`、SSS、QUALITY、
FAST/XXB、礼包、装备共鸣、`巨神兵` / `迅迅迅捷双剑` 等均独立统计。详情无法稳定确认时写
`unknown` 并保留 review artifact，不使用旧合并键或上下文猜测。

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

## 2c. 环境分析报告 — `dzppq-meta-analysis`

基于最新对局库生成环境分析报告。日常推荐直接用 `process_match_batch.py`（标注 + 入库后会自动跑这一步）。单独生成时：

```powershell
python .cursor/skills/dzppq-meta-analysis/scripts/analyze_latest_meta.py
```

可选参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--db` | `data/match_latest.db`（缺失时回退最新 `data/matches_*.db`） | 对局 SQLite |
| `--lookback-days` | `10` | 以最新有效截图批次为终点，分析最近 N 个自然日 |
| `--balance-notes` | 无 | 平衡性调整笔记文件 |
| `--min-comp-apps` | `5` | 阵容发现门槛 |

默认产物：

- `data/latest_meta_analysis.json`
- `data/latest_meta_analysis_report.md`
- `data/latest_meta_analysis_equipment.xlsx`
- `data/环境分析详情.html`（多标签交互页）
- `data/hero-equipment/*.html`（棋子独立装备详情页；从装备面板点击棋子会新开标签页）

阵容推荐只分 **赌狗** / **高费** 两类，输出所有达到发现门槛的策略，不设每类数量上限，也不再拆观察区或高费上限分区。交互页阵容面板仅保留类型筛选。可排序表头贴合表格滚动容器顶部（`th { top: 0 }`），避免偏移覆盖首行。

硬边界：
- 场上任意 1/2/3 费三星棋子 → **赌狗**；成熟阶段若仍建议低费三星主/副 C，也归入赌狗。
- **高费拼多多** 必须无低费三星，并以二星四/五费主 C 为常规成型核心（高费三星仅作成本风险提示）。
- 策略推荐分桶跟随成熟阶段玩法类型，过渡样本比例只保留在审计字段中。

详细规则见 `.cursor/skills/dzppq-meta-analysis/`。

### 桌面三选一卡牌推荐器

推荐器通过界面文字 OCR 匹配具体卡名，再从仅含 0727+ 数据的最新 DB（不可用时回退
JSON）读取该卡自己的指标。所有共用图标卡牌均直接比较具体统计，不再显示或读取“共享统计”。
catalog 中已有但暂时为零样本的具体卡显示“暂无统计”。查询层只保留 OCR 拼写纠正，例如
`开赞` / `开揽` → `蓝·开攒`、`天降啾啾pro` → `蓝·天降揪揪pro`。
新卡一旦进入 DB/JSON 就会自动进入推荐器 catalog，新增卡牌本身不需要修改或重编译 EXE；
`LOGICAL_CARD_KEYS` 只用于零样本预注册。

发布 EXE 必须 clean rebuild，禁止复用旧 `dist` 数据。先刷新 `data/match_latest.db` 与
`data/latest_meta_analysis.json`，再执行：

```powershell
python scripts/build_card_pick_recommender_exe.py --clean
```

构建脚本会在复制后校验外层 `dist/.../data` 与源数据逐字节一致，并检查 JSON
中共享模板拆分/合并后的逻辑键完整、无旧单卡或旧 SSS 排行键；校验失败不会形成可发布构建。

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

### 按卡牌找原始对局截图 — `find_card_matches.py`

按规范卡牌名（支持别名规范化）在统一对局库中检索，输出含该卡的原始截图路径。同一局多名玩家持有该卡时只输出一次；结果按 `captured_at` **升序**（最早在前，最新在末尾），便于从旧到新浏览。

```powershell
python scripts/find_card_matches.py 蓝·满血才是王道
python scripts/find_card_matches.py 蓝·福袋 --db data/match_latest.db --limit 20
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `card_name` | （必填） | 卡牌名；会先走 `normalize_card_label`，再精确匹配 `cards.card_name` |
| `--db` | `data/match_latest.db` | 对局 SQLite（只读） |
| `--limit` | 不限制 | 最多输出多少局（仍按时间升序取前 N 条） |

每条结果先打印采集时间、命中玩家名次/卡槽和相对路径，下一行单独打印解析后的 **绝对 PNG 路径**。在 Windows 终端里可对绝对路径 **Ctrl+Click** 直接打开原始截图。数据库不存在、无匹配、截图文件缺失或 `--limit` 非法时会提示并返回非零退出码；不修改数据库或截图。
