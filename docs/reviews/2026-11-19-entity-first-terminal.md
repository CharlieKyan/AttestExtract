# 实体优先的终端进度

基准为任务开始时的干净提交 `38762cef57d825f5865a3b12c5dc5d24744d73eb`。目标为实验分支 `feature/entity-first-extraction` 的固定标签 `v0.1.0.dev47`。范围包括实时进度、发行版本、公开合成测试与文档。实现者自查及独立只读审阅覆盖计数口径、中断、终端脱敏和普通 CLI 路径。

## 1. 文件职责

| 相对路径 | 修改原因 |
| --- | --- |
| `src/attestextract/entity_first.py` | 在现有登记及属性循环中发送阶段、真实派发、返回和块处理结果。 |
| `src/attestextract/entity_first_output.py`、`src/attestextract/cli.py` | 连接已有运行事件，按组表结果生成最终统计，并控制普通、详细和安静模式的收尾消息。 |
| `src/attestextract/progress.py` | 复用标准错误流和长请求等待提示，按阶段显示预算、耗时、本地检查及报告用量。 |
| `tests/test_entity_first_progress_events.py`、`tests/test_entity_first_terminal.py`、`tests/test_entity_first_entry.py` | 检查事件顺序、预算、截断、缺口、等待、模式及默认命令行输出与实际文件的一致性。 |
| `pyproject.toml`、`src/attestextract/__init__.py`、`README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/entity-first.md`、本摘要和同目录 `README.md` | 同步版本和终端使用说明。 |

产品源码 +135/−17 行，合成测试 +245/−0 行，文档 +52/−2 行，包配置 +1/−1 行。

## 2. 核心行为

此前普通终端能显示模型连接，实体优先分块及账本主要在运行产物中。现在模型请求发出前即显示当前块和共享预算，回答返回后停止等待提示。原文块检查完成后输出经过的时间和各提议的局部判断。最终观测数和正式行数来自本次组表。接口继续使用 v3 科学协议。

## 3. 证据边界

截断和预算耗尽各有相应状态，未派发的块显示实际调用累计数。首轮请求、格式重试和补读从同一账本取数。等待期间收到中断可以保留已发送请求事件；最终是否计费需服务端确认。提议在本块通过检查后仍可能因重复、身份冲突或任务策略而只留明细。

## 4. 验证

完整测试 `python -m pytest -q` 通过 2,735 项，Windows 符号链接权限限制跳过 1 项。全新环境安装 wheel 后，普通 CLI 在本机回环合成服务上完成登记与属性读取两次生成，共报告 176 token，输出一条正式行。四份文档的安装后预检通过，对既有 v3 运行离线重建，各数据集与用户原文件相等。本轮付费生成调用为零。远端 CI 状态随提交另报。

## 5. 人工关注点

- 确认追加一次预算预留后才显示请求，收到回答后停止等待。[调用事件](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev47/src/attestextract/entity_first.py#L141)
- 检查预算、补读、局部计数在普通终端中的含义及慢请求提示。[进度渲染](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev47/src/attestextract/progress.py#L55)
- 对照投影后的正式值和仅留明细的观测。[最终汇总](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev47/src/attestextract/entity_first_output.py#L220)
- 核对默认命令的标准错误流、实际请求及事件安全边界。[CLI 回归](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev47/tests/test_entity_first_entry.py#L89)

## 6. 结论

接受此次终端显示改动。已核对的阻断问题包括无分块进度、等待提示无法启动、实体优先误显可续跑提示和重复打印最终摘要。受模型上报用量及源文可读范围限制，终端仅显示当时已经取得的统计。线上抽取质量评价沿用前一版本的协议和验收。
