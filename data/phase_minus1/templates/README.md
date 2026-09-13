# Phase -1b Template Contract

此目录放真机采样后裁出的模板图。仓库不提供伪造模板，也不预设坐标。

要求：

- 模板尺寸必须与配置中的 ROI `width/height` 完全一致；
- 所有 ROI 坐标都基于 `config/v0.example.json` 中冻结的 `expected_size`；
- 游戏版本、分辨率或 UI 缩放变化后，旧模板必须视为 stale 并重新采样；
- ScreenClassifier 建议每个页面使用 2 个以上相互独立的锚点；
- FactoryReader 的模板只在 `ScreenType.FACTORY` 下参与判定；
- 锚点匹配使用零均值归一化相关系数（NCC），以降低整体亮度/对比度线性变化对匹配的影响；
- 锚点 ROI 应包含稳定纹理/边缘，不要使用纯色块。无纹理常量模板只有像素完全一致时才算匹配；
- 配置中的阈值只能作为初始值。Phase -1b 必须同时采集白天、夜晚、过渡时段以及负样本，
  用真实正负样本分布重新标定每个锚点的 threshold 和 ambiguity margin；
- 不应因为 NCC 对亮度变化更稳就假设 `0.92` 永远合适，阈值仍属于环境基线的一部分；
- 仓库 `used/capacity` 不使用模板识别，走固定 ROI + RapidOCR；RapidOCR 原始置信度也必须
  在真实 Phase -1b 样本上统计后再决定 `V0Policy.min_ocr_confidence`，不要把 `0.99` 当成已验证值。

建议真机产出文件：

```text
factory_anchor.png
factory_idle.png
factory_producing.png
factory_collectable.png
factory_storage_blocked.png
storage_anchor.png
network_error_anchor.png
unknown_popup_anchor.png
```

同一锚点建议至少保留以下校准样本：

```text
positive/day/
positive/night/
positive/transition/
negative/
```

`config/v0.example.json` 中的 `[0, 0, ...]` 坐标全部是占位值，必须由 Phase -1a/1b
真实截图标注替换后才能进入 AUTO。
