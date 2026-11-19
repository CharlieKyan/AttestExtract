# 实体优先的原文定位、字段适配与截断补读

基准为本轮开始时的干净提交 `f9c7fc1065b21b7cec5a82eee915a0c6480aed51`。目标为实体优先分支的固定标签 `v0.1.0.dev44`。本摘要覆盖与代码同批交付的文件。审查包括实现者自查、证据校验与请求调度的交叉只读审阅，以及使用冻结回答的独立离线复核。真实材料、评测脚本与模型回答仅存于私有工作区。

## 1. 变更概览

| 仓库相对路径 | 职责及修改原因 |
| --- | --- |
| `src/attestextract/entity_first_protocol.py` | 解析可见原文格、定位短片段、检查主体和字段适配，分别记录模型提议与程序判断。 |
| `src/attestextract/entity_first.py` | 改进任务示例与批内名单的使用，记录待核提名、待查区域、每次派发和可恢复的截断。 |
| `src/attestextract/entity_first_blocks.py`、`src/attestextract/table_windows.py` | 沿原文范围划分补读块，并限制表内可交付的值格，使不同块共用的上下文可追溯。 |
| `src/attestextract/errors.py`、`src/attestextract/backends.py`、`src/attestextract/responses_backend.py` | 保留两类后端收到的明确截断、已知用量和可脱敏的回答。 |
| `src/attestextract/entity_first_output.py`、`src/attestextract/models.py`、`src/attestextract/cli.py` | 将提名、证据问题与摘要交付到对应视图，核对档案版本，在终端显示覆盖和失败原因。 |
| `tests/test_entity_first_grounding.py`、`tests/test_entity_first_regions.py`、`tests/test_entity_first_truncation.py` | 分别用合成材料检查短引文与语义冲突、表格坐标及无属性回答、请求顺序与费用账本。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 同步发行版本与包内版本。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/entity-first.md` | 记录新协议和导出含义，说明预算、恢复边界及比较方法。 |
| `docs/reviews/README.md`、本摘要 | 提供固定版本的审查记录。 |

相对基准，业务代码新增 543 行、删除 105 行，测试新增 384 行，文档新增 72 行、删除 3 行，打包配置新增和删除各 1 行。

## 2. 核心逻辑变化

- 原先名称与值经常要求引文完全相等。现在先定位完整引文，再在其范围寻找唯一的字面片段，并检查原文边界。未经确认的主体保留其局部提名引用。语义冲突、证据歧义和单位错配影响正式准入，能定位的原值仍可查看。
- 块内的对象、属性和待复核区域可以分别返回。一个完整空回答有独立状态。局部表格索引可以引用已展示的原始单元格，原文坐标仍用于导出和复查。
- 明确截断留下账本和诊断。其他独立属性块优先读取，剩余预算才可用于有界补读。收到不完整 JSON 时整份回答不产出事实。送达不明等故障继续触发全局停机。
- 新运行使用 `entity-first-v2`。重建旧快照时仍按旧快照的证据和判定导出。默认策略仍为观测优先。

## 3. 审阅发现及处理

交叉审阅用“U7/U70”和“80 mm/80 m”发现了依据片段过短时的身份与单位误配。验收同时检查整句引文与短引文。另一项审阅在四个可读块、五次预算下复现了库存阶段的补读挤占后续属性读取的情况，调度现为原始属性读取保留额度。原文定位的成功只增加可追溯性。隐含限定词、跨句转述和专业概念是否等价仍取决于原文及任务定义。

## 4. 测试证据

| 检查 | 实际结果 |
| --- | --- |
| 仓库根目录 `python -m pytest -q` | 最终代码通过 2628 项，跳过 1 项。本机 Windows 账户无法创建该项所需的符号链接。 |
| 从源码包构建 wheel，在独立环境中执行 `pip check`、`attestextract --version` 和 `extract --check` | 依赖通过，版本为 0.1.0.dev44，预检发送 0 次生成请求。 |
| 已安装 CLI 对本机模拟 HTTP 端点运行，再执行 `assemble-dataset` | 3 次请求完成空名册、带属性提名和确认。导出名为 Bench Delta 的一行，数值为 84；离线重建得到相同数据。 |
| 已安装包读取四份私有冻结运行档案 | 六项指定检查通过，历史请求与回答逐包核对。此前未派发的块缺少历史回答，只能另行检验调度。 |
| 旧 v1 快照回读与模拟覆盖 | 四份 v1 快照重建等价；在原任务和相同的空名单模拟回答下，所有可读属性块均成功派发。 |

测试使用合成材料与模拟 HTTP。真实模型端点本轮调用 0 次。冻结回放只评价旧回答的接收和投影，不能测量新提示的线上召回率。对象卡的净收益需另用同条件模型试验判断。推送后的 CI 状态可在对应提交的远程检查中核对。

## 5. 人工审查重点

- [原文坐标与引用窗口](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev44/src/attestextract/entity_first_protocol.py#L110)决定展示格或引文能否定位，重复字面值和不可见格应保持歧义。
- [观测的字段适配与独立归属](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev44/src/attestextract/entity_first_protocol.py#L406)决定长引文能否进入正式输出，尤其需检查单位、范围和断言冲突。
- [实体优先调度和预算](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev44/src/attestextract/entity_first.py#L268)同时约束首轮属性覆盖和补读。
- [补读源范围](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev44/src/attestextract/entity_first_blocks.py#L33)决定哪一个块有权交付值，以及哪些表头可以只读继承。
- [明细与主表投影](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev44/src/attestextract/entity_first_output.py#L38)需保留原始主张、身份和字段各自的依据，避免探索候选被计入正式数据。

## 6. 结论

单对象任务可显式使用该实验策略。已复现的主体边界、单位边界、截断丢账及后续块未派发问题列为本版必修项。当前范围内接受单次在线运行中的有限补读；跨进程在线续跑仍需单独设计。用冻结回答无法评估模型面对新提示和对象卡会怎样作答。默认路线的变更应等待配对试验及人工核对结果。
