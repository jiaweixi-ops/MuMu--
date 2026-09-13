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
- 业务 ADB 输入只能经 `QueueBoundAdbWriter` 进入写队列；直接 `tap/swipe/back`、
  `shell input` 和原始 `run(["shell", "input", ...])` 都会被拒绝。
- `V0Orchestrator` 固定执行 `Observe → Plan → Keeper → Queue → Fresh Verify → Metrics`；
  Verifier 非 `PASS` 时不得推进 Acceptance。
- 动作等待超时后立即 latch ADB 队列 cancel，并等待在途原子动作 drain 完成；在显式恢复前
  不允许下一轮把迟到的设备变化误认成新动作结果。
- ADB 命令有硬超时；重启 adb-server 后 TCP MuMu 设备必须重新 `connect` 并等待上线。
- V0 仓库 OCR 的 `confidence` 默认是 `0.0`；未显式给出可信度即视为不可信。
- V0 默认逐件收取；批量收取留到 V1。
- `WAIT_SESSION_CAP`、`WAIT_STORAGE_RESERVE`、`WAIT_OCR_UNTRUSTED` 语义分离。
- session cap 与 V0 Acceptance 覆盖度均持久化到 SQLite，重启不能绕过额度或清空验收覆盖。
- 每个 `device_id` 同时只允许一个 `RuntimeMetrics` owner；SQLite lease 防止多实例静默覆盖。
- `ASSIST` 使用 `NEEDS_HUMAN`；主循环遇到该状态立即停止轮询，人工批准后仍必须重新经过
  Keeper 其余硬门禁。
- Verifier 保留 `UNKNOWN`；只有 `PASS` 能推进 Task，并保留全部失败原因用于复盘。
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
  runtime/       急停、指标与 V0 Orchestrator 主循环骨架
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
冻结帧比例和黑屏比例。长窗口观察采用流式统计，不保留全部帧哈希。MuMu 应用版本、
窗口/DPI/RDP 行为仍需人工复核并进入基线文档。

## V0 主循环骨架

`runtime/orchestrator.py` 已把基础设施接成一个最小可信闭环：

```text
Fresh Observe
↓
Plan
↓
Keeper + RuntimeMetrics.rate_window
↓
QueueBoundAdbWriter
↓
等待原子动作完成
↓
再次 Fresh Observe
↓
Diff Verifier
↓
PASS 才记录收取/生产成功与 FactoryState 转换
```

当前 `Observer` 与 `Planner` 是协议接口，故意没有伪造游戏视觉实现。真机阶段需要让
Observer 每次调用都获取新的 ADB 截图并生成状态，让 Planner 根据该 Observation 返回
`PlannedAction`。单元测试已经使用真实 Keeper、RuntimeMetrics 与 DeviceCommandQueue
验证接线；这不等于已经在 MuMu 真机上完成端到端验收。

`BLOCKED_STORAGE` 的恢复信号也由主循环机械判定：上一观察必须是
`BLOCKED_STORAGE`，当前观察必须已离开阻塞，并且 `state` 中的 `storage_used`、
`storage_capacity`、`storage_confidence` 可解析，OCR 置信度 `>= 0.99` 且剩余容量 `> 0`，
才会记录 `manual_clear_recovered`。仅页面跳转或低置信度 OCR 不算人工清库恢复。

## V0 Gate

V0 验收不是“没有报错”即可通过，默认同时要求：

- 有效运行时间 `>= 4h`；
- 收取成功 `>= 10`；
- 生产成功 `>= 10`；
- 四条核心 FactoryState 转换各 `>= 3`；
- 人工制造并正确检测 `BLOCKED_STORAGE`；
- 人工清库后成功重新 Observe/Plan；
- 误购、误售、高级货币消费、无限循环、旧状态动作、ADB 写入乱序均为 `0`。

`RuntimeMetrics` 使用 `time.monotonic()` 累加有效运行时间，维护固定的 60 秒动作速率窗口
和固定的 300 秒失败率窗口，并定期把 Acceptance tracker 写入同一个 SQLite Store。
`event_retention_seconds` 只控制事件保留量，不改变“5 分钟失败率”的语义。

计时口径固定如下：

- `WAIT_SESSION_CAP` **计入**有效运行时间：这是 V0 的设计终态，不需要人工干预；
- `BLOCKED_STORAGE` **不计入**有效运行时间，但单独累计 `blocked_storage_seconds`；
- `PAUSED / EMERGENCY_STOP / STOPPED` 同样不计入有效运行时间。

每个 `device_id` 同一时刻只允许一个 `RuntimeMetrics` owner。owner 通过 SQLite lease 周期续租；
异常退出后 lease 到期可被新进程接管，从而避免两个进程互相覆盖 Acceptance 行。

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
