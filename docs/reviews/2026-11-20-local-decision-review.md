# dev49 本地决策复核与可回退的最终表

比较范围为干净的 dev48 基线 `db1e5dcf81d39729bbd22ca9266ff65a0e83baaa` 至固定标签 `v0.1.0.dev49`。新分支 `feature/jev-dataset-review` 承载全部修改。原版本、输出文件和旧版组表入口继续可用。

## 文件与行为

| 文件 | 职责与理由 |
| --- | --- |
| `src/attestextract/jev_packets.py` | 为每条待定或明细观测构造一个本地选择问题。核对任务主体、证据坐标、原值所在陈述及表格范围。缺少可写主体时仍提交复核。 |
| `src/attestextract/jev_backend.py` | 使用用户指定本地检查点的原生 DecisionEngine，延迟读取权重，校验模型名称、选择与完整分布。运行环境和模型文件内容参与缓存身份。 |
| `src/attestextract/jev_field.py`、`tests/test_jev_field.py` | 对拟入表值单独复核字段口径。专项检查输入保留原值和原文，不带第一次模型的答案。 |
| `src/attestextract/jev_review.py` | 校验源文哈希以及整批观测 ID 完整性。按单项事务记录原始响应；复用同一请求和模型的有效回执，逐项错误单列，另存最终数据集。 |
| `src/attestextract/jev_projection.py` | 冻结原有确认行，为选中事实记录模型来源。保留原有身份、条件、原值格、同主体别名；合并时检查同源重复或跨主体的不一致决定。 |
| `src/attestextract/cli.py` | 新增独立命令、进度及输出路径；抽取原有接口继续使用。 |
| `tests/test_jev_packets.py`、`tests/test_jev_backend.py` | 用合成原文检验生成选项的边界，用合成运行时验证原生选择契约、权重指纹与非法答复。 |
| `tests/test_jev_projection.py`、`tests/test_jev_review.py` | 核对逐格来源、弃权、拒绝、恢复、原文件保护、普通 CLI 和部分失败。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 同步包版本；普通抽取无需加载深度学习框架。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/dataset-assembly.md` | 普通用户入口、安装前提、模型输出解释、重试与回退方法。 |
| 本摘要、`docs/reviews/README.md` | 版本绑定的审阅记录与索引。 |

## 人工审阅重点

1. 对原文名字与数值的提议，检查值所在的局部陈述；未指名的比较、否定、组合及不同句的名单可能造成错挂。[局部证据](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev49/src/attestextract/jev_packets.py#L28)
2. 原生模型只在所示选项中打分，核对选择键、分布和分数的一致性。[响应校验](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev49/src/attestextract/jev_backend.py#L121)
3. 同一观察可在多个入口被检索。查证归属、重复与原有确认值后，按原行粒度导出模型裁定值。[结果投影](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev49/src/attestextract/jev_projection.py#L85)
4. 重跑时核对原始文件、模型与候选顺序，已验证的答复从本地账本复用。[复核与交付](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev49/src/attestextract/jev_review.py#L53)
5. 一次模型选择可能认对主体却答错字段。用实际原值复核所指属性；两次模型回答分别留存。仅缺限定的已交付原值在拒绝确认后仍可按待定筛选。[字段复核](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev49/src/attestextract/jev_field.py#L6)

## 验证与范围

实现者自查与独立只读审阅使用可公开的合成正反例。原文和候选同时缺少行主体时，模型仍可决定是否保留待核；开放式补抽、构造新原值和跨文献归并不在本入口范围。模型选择受候选质量影响，分数不能直接换算为事实正确率。

- 审阅中发现的否定陈述、前句名单、分号切换、明确二择一误升，及同一已证主体短名和全名的查询不一致，均已用成对用例复测。新增的 47 项专项测试通过。
- Windows / Python 3.13 对最终冻结源码执行 `python -m pytest -q --tb=short --basetemp <temp>`，2636 项通过，1 项因当前账户无法创建符号链接跳过。
- 真实本地 GPU 用已下载的原生权重执行了合成输入和冻结数据复核。读取安装版导出的主表，核对待处理 ID、逐项原始回答、可追溯的字段判断和原确认值。在线端点调用为零。真实模型结果尚未经独立完整专家标注，不能估计跨任务准确率。
- wheel 在独立环境安装后 `pip check` 和模拟 HTTP 用户入口验收通过，已检查归档代码与源码一致。业务与包元数据 8 个文件（+769/−2），测试 5 个文件（+491），文档含摘要共 6 个文件。暂存补丁经过文件、私有材料、凭据和空白检查。
- 当前结论为可作为显式开启的复核发布。需要有证据的正向候选才能补入；有歧义或仅缺限定时保留原证据和模型判断。可用经专家独立标注的样本另测字段适配准确率。
