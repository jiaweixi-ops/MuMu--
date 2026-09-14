# Map Scan → CityMap → Layout Plan → Verified Construction

本文件定义当前仓库的整城地图识别、布局优化与施工验证软件链路。**这里的“最佳布局”指在当前约束、
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
StagingSolver（纯建筑移动环自动解环）
        ↓
BUILD_ROAD / MOVE_BUILDING / REMOVE_ROAD 有序步骤
        ↓
ConstructionExecutor
        ↓
执行 1 步 → fresh map scan → BuildingIdentityReconciler
        ↓
精确 CityMap diff PASS 才进入下一步
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

## 7. 施工计划、staging 与身份稳定化

`ConstructionPlanner` 将最终目标转换为依赖图：

- 新道路可能需要先移动占位建筑；
- 建筑如果依赖新道路接入，则 BUILD_ROAD 必须先完成；
- 目标位置被其他待移动建筑占用时，存在 move→move 依赖；
- REMOVE_ROAD 默认排在所有建筑迁移和道路新增之后。

### StagingSolver

A/B 建筑互换等纯 MOVE_BUILDING 环现在会进入 `StagingSolver`：

1. 先回放已经排好序的施工前缀，得到真实的当时占用与道路拓扑；
2. 只从当前空闲、非 blocked、非道路、具有道路接入且不占最终目标 footprint 的位置选临时位；
3. 生成 `staging:<building>:N` 临时迁移；
4. 让其他建筑依次让位；
5. 最后把 staging 建筑搬入正式目标位。

如果依赖环包含尚未完成的道路步骤，求解器保持 `unresolved_steps`，不会用猜测顺序穿越混合依赖。

### BuildingIdentityReconciler

模板扫描 ID 会随坐标变化，例如 `residential@12,8 → residential@15,9`。施工时不能把它当成新建筑。
`BuildingIdentityReconciler` 使用 fail-closed 规则恢复稳定 ID：

- 未声明移动的建筑必须仍在原位置；
- 当前步骤声明移动的建筑必须精确出现在目标位置；
- catalog/template 前缀、类别、footprint、人口/覆盖/污染/交通等 fingerprint 必须一致；
- 建筑数变化、缺失、多候选、未知移动全部拒绝，不做最近邻猜测。

这使大量同类住宅在连续重扫时也不会因为“谁离谁近”而静默串 ID。

## 8. ConstructionExecutor：一步一重扫

`ConstructionExecutor` 是通用施工状态机，游戏具体手势由 `ConstructionActionAdapter` 提供，
真实地图重扫由 `LiveCityMapScanner` 提供。状态机本身已经实现：

```text
验证 live baseline
↓
检查 step dependency / source / destination / road precondition
↓
adapter.execute(step)
↓
fresh LiveCityMapScanner.scan()
↓
BuildingIdentityReconciler
↓
计算该步骤唯一允许的 expected CityMap
↓
observed == expected ?
    YES → 记录完成，进入下一步
    NO  → VERIFICATION_FAILED，立即停止
```

精确差分语义：

- MOVE_BUILDING：只允许指定建筑从 source 到 destination；道路、blocked、其他建筑不得变化；
- BUILD_ROAD：只允许指定道路格新增；
- REMOVE_ROAD：只允许指定道路格消失；
- fresh scan / identity reconciliation 失败时不推进；
- action 抛异常时立即停止，不执行下一步；
- `stop_requested` 在步骤前检查；动作已发出后仍先做 fresh verify，再安全停止；
- `ConstructionPlan` 仍有 unresolved steps 时，executor 在接触游戏前直接 `BLOCKED_UNRESOLVED`。

真实 `ConstructionActionAdapter` 接入时仍必须遵守项目既有安全架构：风险动作经过 Keeper，实际 ADB
输入进入 `QueueBoundAdbWriter` 单写队列；不能为施工层重新打开 raw `adb shell input` 旁路。

## 9. 仍需 Phase -1b 真机完成的内容

软件核心链路已经存在，但以下数据和游戏交互不能靠代码猜：

- 实际 MuMu / 游戏分辨率、缩放和地图方向；
- 仿射 origin / X basis / Y basis；
- 道路各方向真实模板；
- 各建筑真实模板与 patch offset；
- blocked / 边界模板；
- 白天、夜晚、昼夜过渡的正样本；
- 易混淆建筑/道路的负样本；
- 每个模板的 threshold 与 ambiguity / 误检统计；
- 各建筑真实 footprint、覆盖、污染、人口、交通负荷等 catalog 数据；
- MOVE_BUILDING 真实 UI/手势 playbook；
- BUILD_ROAD 真实 UI/手势 playbook；
- REMOVE_ROAD 真实 UI/手势 playbook；
- 真实施工后的地图重扫范围、相机定位与地图视野复位流程。

正式 AUTO 前应先用人工标注截图计算 precision / recall，并对 `CityMap` 做人工逐格核对。

## 10. 当前软件边界

截至当前版本，**不依赖真实游戏坐标即可完成的软件核心已经收口**：

```text
截图数字化框架
→ 多视野 CityMap
→ 布局评分
→ 建筑/道路联合优化
→ 施工依赖图
→ staging 解环
→ 跨扫描稳定身份
→ 一步一重扫
→ 精确 map-diff fail-closed verifier
```

下一阶段不是继续虚构更多算法，而是 Phase -1b：把真实模板、仿射标定、真实建筑 catalog 与三个施工
playbook 填进现有接口，然后在独立测试账号上做真机 Gate。没有这些实测数据时，不应伪造
MOVE_BUILDING / BUILD_ROAD / REMOVE_ROAD 的点击坐标或宣称已经完成真机自动重排。
