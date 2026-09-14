# Map Scan → CityMap → Layout Plan

本文件定义当前仓库的整城地图识别与布局优化软件链路。**这里的“最佳布局”指在当前约束、
权重和有限搜索空间内找到的最佳候选，不代表对 SimCity 全局布局问题的数学最优证明。**

## 1. 当前已实现链路

```text
MuMu 真实地图截图（1 张或多张）
        ↓
AffineGridCalibration
        ↓
TemplateRoadDetector
TemplateBlockedCellDetector
TemplateBuildingDetector
        ↓
CalibratedCityMapScanner
        ↓
CityMapMerger（多视野去重/冲突 fail-closed）
        ↓
CityMap JSON
        ↓
LayoutScorer
        ↓
LayoutOptimizer（建筑迁移）
        ↕
RoadTopologyOptimizer（安全道路补齐/瘦身）
        ↓
JointLayoutOptimizer
        ↓
JointLayoutPlan
        ↓
ConstructionPlanner
        ↓
有依赖顺序的 BUILD_ROAD / MOVE_BUILDING / REMOVE_ROAD 步骤
```

软件入口：

```bash
simcity-map-scan --config config/map_scan.example.json --output data/city_map.json
simcity-layout --map data/city_map.json --mode BALANCED --output data/layout_plan.json
```

`map_scan.example.json` 与 `layout.example.json` 只定义 schema / 演示结构，里面的坐标、仿射
基向量、模板路径、阈值和建筑属性都不是 SimCity 的真实测量值。

## 2. 仿射网格标定

SimCity 地图不是普通正交俯视图，因此真实扫描使用 `AffineGridCalibration`：

```text
P(x, y) = origin + x * X_basis + y * Y_basis
```

其中：

- `origin_px`：逻辑格 `(0,0)` 的屏幕锚点；
- `x_basis_px`：逻辑 X 轴移动 1 格对应的屏幕二维向量；
- `y_basis_px`：逻辑 Y 轴移动 1 格对应的屏幕二维向量；
- 两个 basis 必须线性独立；
- `pixel_to_grid()` 使用 2×2 逆变换还原逻辑网格；
- 缩放、旋转、UI 比例、分辨率或游戏版本变化后，应视为 baseline stale，重新标定。

Phase -1b 至少要人工标出 3 个已知逻辑格点，验证仿射模型在当前固定缩放级别下的残差。
若真实地图存在明显非线性透视误差，应升级为分区仿射/单应性，而不是硬调模板阈值掩盖几何错误。

## 3. 地图 detector

### 道路

`TemplateRoadDetector` 可配置多种道路方向/外观模板。每个逻辑格通过 `grid_to_pixel()` 得到
屏幕锚点，再按 `offset_px` 裁出固定 patch，用零均值 NCC 比较。

### 建筑

`TemplateBuildingDetector` 为每种建筑配置：

- `BuildingSpec` 原型；
- 占地宽高；
- 模板；
- patch 相对逻辑原点的像素偏移；
- NCC threshold；
- `id_prefix`。

同一截图中的候选按 NCC 分数排序，然后按逻辑 footprint 做贪心非重叠筛选。快照 ID 形如
`residential@12,8`，它只保证**当前扫描快照内**可确定，不代表跨移动后的永久实体身份。

### 多视野合并

`CityMapMerger` 将每张局部扫描结果按 `grid_origin` 平移到全局地图：

- 相同建筑类型/前缀且 footprint 完全一致 → 去重；
- 两个不同检测结果占地冲突 → `MapMergeConflict`，不猜谁对；
- 道路、blocked cell 使用集合合并；
- 最终仍由 `CityMap` 做边界、建筑重叠、道路/障碍冲突检查。

## 4. CityMap

当前数字城市模型包含：

- 地图宽高；
- 道路格；
- 不可建设格；
- 建筑类别：住宅 / 商业 / 工业 / 服务 / 公园 / 特殊建筑；
- 建筑 footprint；
- population；
- service / beauty radius；
- pollution；
- traffic load；
- movable。

后续真实 catalog 应从游戏实测数据维护，不应把示例值当真。

## 5. 布局评分

`LayoutScorer` 当前综合：

- road access；
- service coverage；
- beauty coverage；
- pollution separation；
- traffic efficiency；
- zone compactness；
- expansion space；
- road efficiency；
- move penalty。

预设模式：

- `BALANCED`
- `POPULATION`
- `SERVICE`
- `BEAUTY`
- `EXPANSION`

这些只是优化目标权重，不是游戏官方评分公式。真实人口加成、专业化建筑覆盖、道路拥堵等规则取得
可信数据后，应继续扩充 scorer。

## 6. 联合优化

### 建筑

`LayoutOptimizer` 使用确定性的坐标下降搜索：逐个 movable building 枚举可放置且有道路接入的
候选位置，只接受总评分提升的迁移，并保留原始布局作为 move-penalty 基线。

### 道路

`RoadTopologyOptimizer` 采用保守策略：

1. 对无道路接入建筑，尝试用 BFS 找到不穿建筑/blocked 的最短 connector；
2. 删除道路时必须同时满足：
   - 剩余道路图保持连通；
   - 所有建筑仍有 road access；
   - 总评分提升。

它不是任意道路网络的全局搜索，因此不能宣称找到了全局最优路网。

### 联合

`JointLayoutOptimizer` 在固定原始 move-cost baseline 下交替执行建筑迁移和道路优化，直到没有
足够提升或达到轮数上限。

## 7. 施工计划

`ConstructionPlanner` 将最终目标转换为依赖图：

- 新道路可能需要先移动占位建筑；
- 建筑如果依赖新道路接入，则 BUILD_ROAD 必须先完成；
- 目标位置被其他待移动建筑占用时，存在 move→move 依赖；
- REMOVE_ROAD 默认排在所有建筑迁移和道路新增之后。

若出现 A/B 建筑互换等依赖环，系统**不会伪造可执行顺序**，而是：

```text
requires_staging = true
unresolved_steps = [...]
```

后续真机施工层必须先找到并验证临时空地，再解除该环。

## 8. 仍需 Phase -1b 真机完成的内容

软件链路已存在，但以下数据不能靠代码猜：

- 实际 MuMu / 游戏分辨率、缩放和地图方向；
- 仿射 origin / X basis / Y basis；
- 道路各方向真实模板；
- 各建筑真实模板与 patch offset；
- blocked / 边界模板；
- 白天、夜晚、昼夜过渡的正样本；
- 易混淆建筑/道路的负样本；
- 每个模板的 threshold 与 ambiguity / 误检统计；
- 各建筑真实 footprint、覆盖、污染、人口、交通负荷等 catalog 数据。

正式 AUTO 前应先用人工标注截图计算 precision / recall，并对 `CityMap` 做人工逐格核对。

## 9. 当前尚未实现的最后一段

`ConstructionPlan` 目前是**离线施工计划**，还没有接入真机 ADB 的：

- MOVE_BUILDING playbook；
- BUILD_ROAD playbook；
- REMOVE_ROAD playbook；
- 每一步 fresh screenshot / map-diff verifier；
- staging 自动求解与施工；
- 建筑跨帧永久身份 reconciliation。

这些必须基于真实游戏 UI 行为与真机截图施工，不能使用猜测坐标。
