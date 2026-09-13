# Phase -1b Template Contract

此目录放真机采样后裁出的模板图。仓库不提供伪造模板，也不预设坐标。

要求：

- 模板尺寸必须与配置中的 ROI `width/height` 完全一致；
- 所有 ROI 坐标都基于 `config/v0.example.json` 中冻结的 `expected_size`；
- 游戏版本、分辨率或 UI 缩放变化后，旧模板必须视为 stale 并重新采样；
- ScreenClassifier 建议每个页面使用 2 个以上相互独立的锚点；
- FactoryReader 的模板只在 `ScreenType.FACTORY` 下参与判定；
- 阈值默认 `0.92`，真机样本集应同时包含正样本与负样本后再调整；
- 仓库 `used/capacity` 不使用模板识别，走固定 ROI + RapidOCR。

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

`config/v0.example.json` 中的 `[0, 0, ...]` 坐标全部是占位值，必须由 Phase -1a/1b
真实截图标注替换后才能进入 AUTO。
