# SimCity AI Mayor

MuMu 模拟器上的《模拟城市：我是市长》自动化与城市布局优化工程。

当前工程分成两条已经接通的软件链路：

```text
A. V0 自动化闭环
ADB 截图 → 页面/工厂/仓库识别 → Planner → Keeper → ADB Queue
→ Fresh Screenshot → Verifier → Metrics / Recovery

B. 地图布局链路
真实地图截图 → 仿射网格标定 → 道路/建筑模板识别 → 多视野合并 → CityMap
→ LayoutScorer → 建筑+道路联合优化 → ConstructionPlan
```

项目仍坚持 **fail-closed**：无法确认页面、仓库状态、地图识别或施工条件时，不猜、不点击。

## 当前状态

### V0 自动化软件层

已实现：

- `AdbScreenshotObserver` 真实 ADB 截图；
- `frame_id + captured_at` Fresh-State 机械校验；
- 页面多锚点 NCC 识别；
- FactoryState 模板识别；
- 固定 ROI + RapidOCR 仓库 `used/capacity`；
- OCR 原始置信度与跨帧 measurement confidence 分离；
- 仓库 capacity 异常下降、无动作解释的 `used` 跳变 fail-closed；
- `CITY → FACTORY` 导航动作；
- 单件收取与基础生产；
- Keeper 风险门禁、retry、cooldown、failure-rate、动作速率限制；
- 单设备单 ADB writer queue；
- 动作 timeout 的 cancel→drain→reopen；
- Ctrl+C / stop.flag / Windows Ctrl+Alt+F12 急停闩锁；
- Observe / Plan / Fresh Observe 异常恢复与指数退避；
- `run_for(duration_seconds)` 有界运行；
- SQLite session cap、Acceptance、lease；
- 生产配额采用“写入前保守预留；可信 fresh verifier 明确 FAIL 才回滚”，避免崩溃/超时漏记；
- CLI：`simcity-v0`。

### 地图布局软件层

已实现：

- 正交 `GridCalibration` 与等距/斜视 `AffineGridCalibration`；
- `TemplateRoadDetector`；
- `TemplateBlockedCellDetector`；
- `TemplateBuildingDetector`；
- 多视野 `CityMapMerger`，重叠矛盾检测 fail-closed；
- `CityMap` 数字城市模型；
- `LayoutScorer` 多目标评分；
- 建筑位置 `LayoutOptimizer`；
- 道路接入修复与冗余道路安全删除 `RoadTopologyOptimizer`；
- 建筑+道路 `JointLayoutOptimizer`；
- `ConstructionPlanner` 生成 BUILD_ROAD / MOVE_BUILDING / REMOVE_ROAD 依赖图；
- 建筑互换等依赖环不伪造顺序，而是标记 `requires_staging=true`；
- CLI：`simcity-map-scan`、`simcity-layout`。

“最佳布局”当前含义是：**在配置的评分目标、约束和有限搜索空间内找到最佳候选**，不是数学意义上证明的全局最优。

## 安装

要求 Python **3.11+**。

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

真机 OCR / vision：

```bash
python -m pip install -e ".[vision]"
```

`vision` extra 显式包含 `numpy`、`opencv-python` 与 `rapidocr-onnxruntime`。

## Phase -1a：MuMu 环境基线

真实设备首次接线先运行：

```bat
python -m simcity_ai_mayor.phase1.probe ^
  --device-id 127.0.0.1:<实际端口> ^
  --package <真实包名> ^
  --samples 100 ^
  --watch-seconds 120
```

冻结：

- ADB device id / MuMu 端口；
- package / versionName；
- Android / SDK；
- screenshot 尺寸；
- DPI / 方向；
- 黑屏、冻结帧、RDP 断连/重连行为。

不要把示例中的 `127.0.0.1:7555` 当成已验证真实端口。

## Phase -1b：视觉与地图标定

### 页面 / Factory / 仓库

需要真实样本完成：

- CITY / FACTORY 等页面锚点；
- IDLE / PRODUCING / COMPLETED_COLLECTABLE / COMPLETED_STORAGE_BLOCKED 模板；
- Storage ROI；
- collect / production / enter-factory 点击坐标；
- 白天、夜晚、昼夜过渡和负样本阈值统计；
- RapidOCR engine confidence 与 measurement confidence 分布。

模板匹配使用零均值 NCC，因此对整体亮度/对比度线性变化比原始 MAD 稳定，但 threshold 仍必须由真实样本标定。

### 整城地图

SimCity 地图使用斜视/等距视觉，真实扫描使用二维仿射格：

```text
P(x,y) = origin + x * X_basis + y * Y_basis
```

配置参考：

```text
config/map_scan.example.json
```

示例里的 origin、basis、offset、模板路径和 threshold 全部是占位结构，不是真实 SimCity 数据。

详细契约见：

```text
docs/MAP_LAYOUT_PIPELINE.md
```

## V0 运行

配置参考：

```text
config/v0.example.json
```

完成 Phase -1a / -1b 后，先保持 `DRY_RUN`：

```bat
simcity-v0 --config config\v0.local.json --duration-seconds 600
```

再逐步进入 `ASSIST`，最后才进入 `AUTO`。

主循环：

```text
Fresh Observe
↓
Plan
↓
Keeper
↓
QueueBoundAdbWriter
↓
等待原子动作结束
↓
Fresh Observe
↓
frame_id / captured_at Freshness Gate
↓
Diff Verifier
↓
PASS 才推进 Acceptance
```

## 仓库可信度

仓库识别现在分两层：

```text
RapidOCR engine confidence
↓
ValidatedStorageReader
↓
跨帧稳定性 + 合法转移约束
↓
measurement confidence
↓
StorageCapacity.confidence
```

重复相同读数会融合测量置信度；例如两帧独立 `0.95` 可形成高于 `0.99` 的 measurement confidence。

状态约束包括：

- `capacity > 0`；
- `0 <= used <= capacity`；
- capacity 不允许无解释下降；
- 收取前登记期望 `used +1`；
- 无动作解释的 `used` 跳变降为不可信；
- 从已确认“仓库满”状态人工清库后的下降允许安全 rebase。

`V0Policy.min_ocr_confidence` 当前仍需 Phase -1b 真机样本校准，不应把示例值 `0.99` 视为最终结论。

## 生产配额语义

为了防止“设备动作发生但程序崩溃导致额度漏记”，生产动作在 ADB 点击前先持久化预留额度：

```text
reserve quota
→ ADB tap
→ fresh verify
```

只有在**可信 fresh frame + verifier 明确 FAIL**时才释放额度。Timeout、Fresh Observe 失败、UNKNOWN 等状态保持预留，因为设备真实结果无法确认。

## 地图扫描

真实截图、真实标定和真实模板准备好后：

```bat
simcity-map-scan ^
  --config config\map_scan.local.json ^
  --output data\city_map.json
```

扫描器：

```text
截图
→ AffineGridCalibration
→ 道路格模板扫描
→ 建筑原点模板扫描
→ blocked cell 扫描
→ 局部 CityMap
→ 多视野 CityMapMerger
→ city_map.json
```

如果重叠视野对同一 footprint 给出互相冲突的建筑结果，合并器直接失败，不投票猜测。

## 布局优化

可以直接对 `CityMap JSON` 做离线规划：

```bat
simcity-layout ^
  --map data\city_map.json ^
  --mode BALANCED ^
  --output data\layout_plan.json
```

支持目标模式：

- `BALANCED`
- `POPULATION`
- `SERVICE`
- `BEAUTY`
- `EXPANSION`

当前评分维度：

- road access；
- service coverage；
- beauty coverage；
- pollution separation；
- traffic efficiency；
- zone compactness；
- expansion space；
- road efficiency；
- move penalty。

道路优化采用保守策略：

1. 无道路接入建筑 → BFS 补最短安全 connector；
2. 删除道路必须保持路网连通；
3. 所有建筑仍需保留 road access；
4. 总评分必须提升。

因此当前不是任意道路拓扑的全局穷举，而是安全的局部联合优化。

## ConstructionPlan

布局输出不仅有最终坐标，还会生成施工依赖：

```text
BUILD_ROAD
MOVE_BUILDING
REMOVE_ROAD
```

规则包括：

- 建筑目标格被另一待移动建筑占用 → 等对方先移走；
- 新道路位于建筑旧 footprint → 先移动建筑；
- 建筑最终接入依赖新道路 → 先建道路；
- 拆路默认放在所有迁移和新增道路之后。

若存在建筑互换导致的依赖环，输出：

```text
requires_staging = true
```

系统不会生成实际上无法执行的“直接互换”步骤。

## 当前还缺什么

代码已经形成完整的软件链路，但**还不能宣称真机整城自动改造完成**。剩余依赖真实游戏数据的工作主要是：

1. Phase -1a 真机环境冻结；
2. Phase -1b 页面、仓库、地图仿射参数与模板采样；
3. 用人工标注真实截图测 precision / recall，并校准 threshold；
4. 建立真实 `BUILDING_CATALOG`：占地、人口、覆盖、污染、交通等；
5. 实现真机 `MOVE_BUILDING / BUILD_ROAD / REMOVE_ROAD` ADB playbook；
6. 每个施工步骤做 fresh screenshot + map-diff verify；
7. 自动 staging 求解；
8. 建筑跨帧身份 reconciliation；
9. 最后执行 V0 4 小时真机 Gate 与布局施工专项验收。

这些步骤不能靠猜坐标替代真实采样。

## 运行时硬约束

- 默认 `DRY_RUN`；
- Keeper 页面白名单，未知页面默认拒绝；
- 一个 `device_id` 只有一个 ADB writer queue；
- 原始 ADB input 绕过 writer 会被拒绝；
- Verifier 只有 `PASS` 能推进；
- timeout 使用瞬时 cancel→drain→reopen；
- emergency stop / Ctrl+C 保持 writer cancelled 闩锁；
- `NEEDS_HUMAN` 停止主循环；
- `OBSERVE_FAILED / EXECUTION_FAILED / VERIFICATION_FAILED` 使用指数退避；
- retry 按 `(task_id, action_name)` 作用域；
- `RETRY_LIMIT` 会启动显式 cooldown；
- session cap 与 Acceptance 持久化，重启不能绕过；
- `RECOVER / BLOCKED_STORAGE / PAUSED / EMERGENCY_STOP / STOPPED` 不计有效运行时间；
- 不设计反检测或规避平台风控能力。

## V0 Gate

V0 真机验收仍要求至少：

- 有效运行时间 `>= 4h`；
- 收取成功 `>= 10`；
- 生产成功 `>= 10`；
- 四条核心 FactoryState 转换各 `>= 3`；
- 强制制造并检测 `BLOCKED_STORAGE`；
- 人工清库后恢复；
- 误购、误售、高级货币消费、无限循环、旧状态动作、ADB 写乱序均为 `0`。

## 测试

```bash
ruff check src tests
pytest
```

CI 矩阵覆盖：

- Ubuntu + Python 3.11
- Ubuntu + Python 3.12
- Windows + Python 3.11
- Windows + Python 3.12

当前代码批次已达到 **111 tests**。

## 目录

```text
src/simcity_ai_mayor/
  core/          状态、模型、V0 policy、Acceptance
  device/        ADB 与单设备串行队列
  executor/      Keeper
  verifier/      State/Diff verifier
  storage/       SQLite session / quota / acceptance
  runtime/       V0 orchestrator、metrics、急停、组合根
  planner/       V0 导航/收取/生产 Planner
  vision/        Observer、OCR、页面模板、地图扫描与合并
  city/          CityMap 数字城市模型与 JSON codec
  layout/        scorer、建筑/道路联合优化、施工依赖图
  phase1/        Phase -1a probe
config/          V0 / map scan / layout 示例配置
docs/            Gate 与地图布局文档
data/            Phase -1a / -1b 数据目录
tests/           回归测试
```

## License

仓库暂未选择开源许可证。由仓库所有者确定授权方式后再加入 `LICENSE`。

> 账号与合规：在线游戏自动化可能违反游戏或平台规则。工程不承诺账号安全；应使用独立测试账号并自行核对适用条款。
