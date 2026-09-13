# SimCity AI Mayor

MuMu 模拟器上的《模拟城市：我是市长》自动化工程。

当前仓库按 **V1.3 Architecture Freeze / Construction Baseline** 开工，先实现 Phase -1a、Phase 0 和 V0 的最小可信闭环，不追求高产量。

## 当前施工原则

- 默认 `DRY_RUN`，未通过 Gate 的 Task 不允许执行。
- `device_id` 从第一天贯穿日志、数据库、Keeper 和截图目录。
- 同一设备只有一个 ADB 写命令队列；Guardian 不与 Executor 并发写 ADB。
- 每条 ADB 命令有硬超时；超时后允许重启 adb server 并重连。
- V0 只识别仓库 `used/capacity`，不假装拥有逐物品库存。
- V0 默认按“逐件收取”设计；批量收取留到后续优化。
- 收取前必须满足 `free >= 1 + collect_safety_margin`。
- V0 生产预算持久化到 SQLite，程序、游戏、ADB 或 Windows 重启不会自动清零。
- `WAIT_SESSION_CAP` 与 `WAIT_STORAGE_RESERVE` 分离。
- 不确定即不操作；未知页面只允许已验证的 Back/Return-City playbook。
- 不设计反检测或规避平台风控能力。

## 目录

```text
src/simcity_ai_mayor/
  core/          状态、模型、V0 策略
  device/        ADB 命令与串行执行
  executor/      Keeper
  verifier/      可组合断言
  storage/       SQLite / V0 session 持久化
  runtime/       急停与运行时控制
  phase1/        Phase -1a 环境探测

tests/           单元测试
```

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

## 运行基础自检

```bash
python -m simcity_ai_mayor.phase1.probe --device-id <adb-device-id>
```

## 运行测试

```bash
pytest
```

## V0 Gate（当前实现目标）

V0 不是“挂 4 小时什么都不出错”就算通过。正式验收必须同时满足：

- 测试前仓库满足安全水位；
- 至少发生真实生产与真实收取；
- 覆盖 `IDLE -> PRODUCING -> COMPLETED_COLLECTABLE -> COLLECTED -> IDLE`；
- 误购、误售、高级货币消费、无限点击、旧状态继续执行、ADB 并发乱序均为 0；
- `BLOCKED_STORAGE` 能正确停机；
- 人工清库后丢弃旧任务并重新 Observe/Plan。

> 账号与合规：在线游戏自动化可能违反游戏或平台规则。此工程不承诺账号安全，开发时应使用独立测试账号并自行核对适用条款。
