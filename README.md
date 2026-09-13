# SimCity AI Mayor

MuMu 模拟器上的《模拟城市：我是市长》自动化工程。

当前仓库按 **V1.3 Architecture Freeze / Construction Baseline** 施工。目标是先证明：
**真实状态可观测 → Keeper 失效安全 → 单线程 ADB 执行 → Fresh-State 验证 →
异常可停止/恢复**，而不是先追求高产量。

## 当前硬约束

- 默认 `DRY_RUN`；未通过 Gate 的 Task 不执行。
- Keeper 对页面采用**白名单**：新增 `ScreenType` 默认拒绝。
- `device_id` 贯穿 ActionRequest、ADB、数据库和运行时状态。
- 同一设备只有一个有界 ADB 写队列，并维护单调 `seq`。
- ADB 命令有硬超时；重启 adb-server 后 TCP MuMu 设备必须重新 `connect` 并等待上线。
- V0 仓库 OCR 的 `confidence` 默认是 `0.0`；未显式给出可信度即视为不可信。
- V0 默认逐件收取；批量收取留到 V1。
- `WAIT_SESSION_CAP`、`WAIT_STORAGE_RESERVE`、`WAIT_OCR_UNTRUSTED` 语义分离。
- session cap 与 V0 Acceptance 覆盖度均持久化到 SQLite，重启不能绕过额度或清空验收覆盖。
- `ASSIST` 使用 `NEEDS_HUMAN`，人工批准后仍必须重新经过 Keeper 其余硬门禁。
- Verifier 保留 `UNKNOWN`；只有 `PASS` 能推进 Task。
- 急停通道健康状态必须可见；`stop.flag` 使用绝对路径。
- 不设计反检测或规避平台风控能力。

## 目录

```text
src/simcity_ai_mayor/
  core/          状态、模型、V0 策略与验收口径
  device/        ADB 命令与单设备串行队列
  executor/      Keeper 安全闸门
  verifier/      State/Diff 可组合断言
  storage/       SQLite session + acceptance 持久化
  runtime/       急停
  phase1/        Phase -1a 环境探测
docs/            Gate 文档骨架
playbooks/       交互剧本
data/phase_minus1/  采样目录说明
tests/           单元测试
```

## 安装

要求 Python **3.11+**。

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Phase -1a 基础探测

`--package` 是 Gate 必填项。建议 RDP/无人值守机器使用 `--watch-seconds`，
在观察窗口中主动做 RDP 断连/重连测试。

```bash
python -m simcity_ai_mayor.phase1.probe ^
  --device-id 127.0.0.1:7555 ^
  --package <真实包名> ^
  --samples 100 ^
  --watch-seconds 120
```

探测器自动记录 Android 版本、SDK、方向相关 dump、截图尺寸漂移、游戏版本、
冻结帧比例和黑屏比例。MuMu 应用版本、窗口/DPI/RDP 行为仍需人工复核并进入基线文档。

## V0 Gate

V0 验收不是“没有报错”即可通过，默认同时要求：

- 有效运行时间 `>= 4h`；
- 收取成功 `>= 10`；
- 生产成功 `>= 10`；
- 四条核心 FactoryState 转换各 `>= 3`；
- 人工制造并正确检测 `BLOCKED_STORAGE`；
- 人工清库后成功重新 Observe/Plan；
- 误购、误售、高级货币消费、无限循环、旧状态动作、ADB 写入乱序均为 `0`。

验收 tracker 使用与 session cap 相同的 SQLite Store 持久化，程序重启后继续累计，
不会随机清零。

## 测试

```bash
ruff check src tests
pytest
```

CI 同时运行 Python `3.11` / `3.12`，并覆盖 `ubuntu-latest` 与 `windows-latest`。

## License

仓库暂未选择开源许可证。由仓库所有者确定授权方式后再加入 `LICENSE`，避免代替所有者
做不可逆的许可选择。

> 账号与合规：在线游戏自动化可能违反游戏或平台规则。此工程不承诺账号安全，
> 开发时应使用独立测试账号并自行核对适用条款。
