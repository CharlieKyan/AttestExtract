# 首页科研证据与模型接入说明

- 审查日期为 2026-11-24。基准是任务开始时的 `ae7b989324539211c8c57640ee7841abc5e284a5`，当时工作区干净。
- 审查目标为预览分支 `release/0.1.0a1` 上的首页提交 `7f3c792d7c89442a4f0de860819c6f0dbb03f7e0`。本摘要和索引在随后的文档提交中归档。
- 审查来源包括实现者自查，以及对上游数据结构、科研能力边界、两种语言的表述和连接示例所做的独立只读审阅。结论为当前预览可接受。

## 1. 逐文件改动

| 文件 | 本次用途 |
| --- | --- |
| `README.md` | 说明逐字段证据、表格和归属复核；展示四个预设、三种连接方式、Python 调用与离线审阅。 |
| `README.zh-CN.md` | 提供相同的中文说明、可对照的命令和导航。 |
| `docs/reviews/2026-11-24-homepage-evidence-models.md` | 记录上游对照范围和示例验证证据。 |
| `docs/reviews/README.md` | 加入本摘要的索引。 |

业务代码、配置和测试文件改动均为 0 行。两份首页合计新增 158 行、删除 8 行，另有本摘要与索引两份归档文档。

## 2. 读者能做什么

此前首页只有一条 DeepSeek 实跑命令。现在可以选择预设、兼容网关或 TOML 命名配置，从命令行或 Python 复用原文和任务；抽取后可离线生成适合阅读的审阅稿。包版本仍为 `0.1.0a1`，此次变更仅涉及文档。

## 3. 核对过的技术边界

- LangExtract v1.7.2 的字符区间和对齐状态属于整条抽取，属性以字典保存。NICEExtract 为正文字段分别保存引文和匹配状态。上游也提供原文定位、长文分块和批次缓存恢复，首页仅对照可核实的数据结构。[版本化数据结构](https://github.com/google/langextract/blob/v1.7.2/langextract/core/data.py#L64-L92) · [上游批处理恢复说明](https://github.com/google/langextract/blob/494e285b176a3a0e76dde53a729c01e21bc155b9/docs/examples/batch_api_example.md#L131-L135)
- 首页区分表格的局部位置检查与观测的语义归属。字段清单任务显式启用的额外查证有独立预算，产生模型建议，允许未知。只有服务明确报告输出长度截断才描述表格按行或单元格细分。
- 比较模型时创建新运行；恢复旧运行需兼容原任务和模型。内置通义预设可选择不同凭据变量，仓库示例中的命名 `qwen` 配置则明确读取 `DASHSCOPE_API_KEY`。

## 4. 实际验证

| 检查方式 | 结果及范围 |
| --- | --- |
| 从 README 读取四条 `niceextract extract` 命令并追加 `--check` | 使用临时目录、合成原文、任务与占位密钥。两个 Chat 预设、自定义网关、命名配置都给出一个分块，网络调用均为 0。 |
| 执行 README 的 Python 代码块，替换一次模型标识 | 屏蔽外网并使用模拟 HTTP，两个预设分别选择了预期的主机、模型、凭据。输出 JSON 均可解析，候选带原文引句。 |
| `niceextract export-review --input examples/ccus-result.json --output-dir review-example` | 在临时目录用随附单篇示例生成 `annotation.md` 和可解析的 `annotation.json`。 |
| 两版结构、页内锚点、相对链接、代码块比对及 `git diff --check` | 两版各有 13 个标题、25 个链接、7 个相同代码块。已核对本地目标、章节锚点以及上游标签中的实际字段行号，排版检查通过。 |

本轮仅改文档，本地未重跑全量 pytest 或重建安装包。真实端点调用为 0，也未进行跨框架准确率或速度对照。归档编写时此次提交待推送，远端 CI 状态以推送后的运行记录为准。

## 5. 人工复核入口

- [README.zh-CN.md 第 17 行](https://github.com/CharlieKyan/AttestExtract/blob/7f3c792d7c89442a4f0de860819c6f0dbb03f7e0/README.zh-CN.md#L17) · 上游对照和四个科研问题是否说清证据与判断的区别。
- [README.zh-CN.md 第 57 行](https://github.com/CharlieKyan/AttestExtract/blob/7f3c792d7c89442a4f0de860819c6f0dbb03f7e0/README.zh-CN.md#L57) · 当前预设、凭据变量和两个输出文件是否便于对照。
- [README.md 第 83 行](https://github.com/CharlieKyan/AttestExtract/blob/7f3c792d7c89442a4f0de860819c6f0dbb03f7e0/README.md#L83) · Notebook 用户能否用同一份任务切换模型并取得 JSON。
- [README.zh-CN.md 第 101 行](https://github.com/CharlieKyan/AttestExtract/blob/7f3c792d7c89442a4f0de860819c6f0dbb03f7e0/README.zh-CN.md#L101) · 自定义服务与命名配置分别需要哪些地址、模型名和密钥变量。

## 6. 结论

未发现必须修复的文档问题，当前可用于预览。对真实模型或不同框架的效果判断需要单独的对照实验。本次采用现有预览分支发布，默认首页按项目负责人的审阅安排切换。
