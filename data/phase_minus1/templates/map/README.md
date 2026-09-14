# Phase -1b Map Template Contract

本目录只接受由**真实 MuMu + 真实 SimCity 游戏画面**裁出的地图模板。仓库不提供伪造模板。

## 1. 固定环境

采样前先冻结：

- game `versionName`；
- screenshot width / height；
- 地图缩放级别；
- 地图方向；
- Android / MuMu DPI；
- UI 缩放；
- RDP / 本地显示行为。

任一项变化后，仿射标定和模板都应视为 stale，重新验证。

## 2. 仿射格标定

真实地图扫描使用：

```text
P(x,y) = origin + x * X_basis + y * Y_basis
```

至少人工标出 3 个非共线、逻辑坐标已知的格点，并记录：

```text
origin_px
x_basis_px
 y_basis_px
```

建议再选择 10+ 个散布在视野内的验证格点，计算 `grid_to_pixel()` 的像素残差。

如果残差随离中心距离明显增加，不要继续硬调模板阈值，应改用：

- 更小的局部 tile；
- 分区仿射；
- 或更高阶的几何模型。

## 3. 道路模板

道路应按可见外观分别采样，例如：

```text
road_straight_a.png
road_straight_b.png
road_corner_*.png
road_intersection_*.png
```

具体是否需要分方向，以真实截图为准。每一种 road patch 需记录相对于逻辑格锚点的
`offset_px`。

必须同时准备：

- day positive；
- night positive；
- transition positive；
- adjacent non-road negative；
- 与地面颜色相近的 hard negative。

## 4. 建筑模板

每个 V1 要识别的建筑至少维护：

```text
canonical_name
id_prefix
BuildingKind
footprint width/height
population
service_radius
beauty_radius
pollution
traffic_load
movable
template file
offset_px
threshold
```

`footprint` 必须以游戏逻辑格实测，不得由截图像素尺寸猜测。

建筑模板尽量选择：

- 稳定轮廓；
- 独特屋顶/图标；
- 不被浮动 UI 覆盖的区域；
- 对昼夜亮度变化稳定的纹理。

避免：

- 烟雾、粒子、车辆等动态元素；
- 大片纯色；
- 水面反光；
- 可变化的任务气泡；
- 只在某一动画帧出现的细节。

## 5. blocked / 不可建格

只有当不可建区域在固定视角下有稳定视觉特征时才使用模板检测。地图边缘、水域、山体等
若无法由固定 patch 稳定区分，应改由独立 mask / 人工 baseline 数据描述，不要强行模板化。

## 6. 阈值标定

模板匹配使用零均值 NCC。每个模板的 threshold 必须基于真实样本：

```text
positive/day/
positive/night/
positive/transition/
negative/
hard_negative/
```

至少统计：

- 正样本最低分；
- 正样本 P05 / P50；
- 负样本最高分；
- hard-negative 最高分；
- 建议 threshold；
- precision / recall。

不要因为示例配置写着 `0.92` 就直接使用 `0.92`。

## 7. 多视野扫描

全城通常无法单张截图覆盖。每张 tile 必须同时保存：

```text
tile image
local grid size
global grid_origin
origin_px
X_basis
Y_basis
zoom / orientation / game version
```

相邻 tile 应有足够重叠区域，用于验证：

- 道路全局坐标一致；
- 同一建筑 footprint 一致；
- `CityMapMerger` 能正确去重；
- 没有 overlap conflict。

出现冲突时应修正标定或 detector，不能在 merger 里“多数投票”掩盖问题。

## 8. AUTO 前最低验收

地图扫描进入自动施工前，至少人工审核：

- road precision / recall；
- building precision / recall；
- footprint accuracy；
- global grid alignment；
- overlap merge consistency；
- day/night consistency；
- `CityMap` 中不存在建筑与 road/blocked 重叠；
- 同一张截图重复扫描结果稳定。

只有这些通过，`simcity-map-scan` 的输出才允许进入自动布局施工链路。
