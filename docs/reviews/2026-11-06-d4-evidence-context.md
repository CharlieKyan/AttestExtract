# 审查摘要：D4 原文结构、上下文与回归实验

- 审查日期：2026-11-06。
- 比较基准：`a95bf81b05e3ffb2232356497585ef275bf8060a`（本次实施开始时 HEAD，dev18）；开始时工作区干净。先前规划基于 dev17，本轮保留 dev18 的离线决策功能。
- 审查目标：`v0.1.0.dev19`，代码与本摘要同提交；覆盖本轮全部暂存改动，不含外部研究目录、论文、标签、模型原始输出和运行日志。
- 审查方式：实现者自查；独立代理交叉只读审阅上下文切分、证据/归档、实验脚本；根代理集成审查。自编模块的自查未计作该模块的独立审阅。
- 总结论：已确认的阻断问题已修复。软件验收通过；新增上下文默认关闭，真实模型质量的总体净收益尚未证实。
- 版本与发布：包 dev19，结果 schema 0.4，模型协议仍 flat-v1。摘要冻结时待提交、注释标签与普通推送；对应 GitHub CI 尚未触发，最终状态以该标签提交和交付记录为准。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| `src/attestextract/structure.py`、`documents.py` | 增加原文结构索引与可选文档结构；保留 HTML/Markdown 网格、显式表头提示、唯一对齐的 MinerU 元数据。 |
| `src/attestextract/contextual_chunking.py`、`chunking.py`、`planning.py` | 主体与辅助上下文分离；章节、表格与相邻段落；覆盖诊断、不可分行组及递归位置映射。 |
| `src/attestextract/normalization.py`、`evidence.py` | 可回读的保守检索视图、完整展示片段内定位、统一表格列关系。 |
| `src/attestextract/models.py`、`identity.py`、`archive.py` | schema 0.4、原文命中和单元格引用、上下文出处；历史身份和机械检查兼容。 |
| `src/attestextract/pipeline.py`、`prompting.py`、`execution.py` | 策略入口、分角色模型输入、主体责任检查、覆盖产物和恢复指纹。 |
| `src/attestextract/decision_bundle.py`、`reporting.py` | 决策材料保留实际片段范围及主体归属；报告显示原文、规范化规则、单元格和上下文。 |
| `src/attestextract/cli.py`、`__init__.py`、`pyproject.toml` | 可选 context/MinerU/hints 接口、公开类型和包版本。 |
| `scripts/d4_context_experiment.py`、`scripts/d4_evidence_smoke.py`、`scripts/protocol_evidence_smoke.py` | 冻结四臂实验、独立安装 D4 验收、更新当前结果版本断言。 |
| `tests/test_context_chunking.py`、`test_d4_integration.py`、`test_document_structure.py` | 独立边界预期、覆盖、CLI、截断恢复与决策材料集成。 |
| `tests/test_d4_evidence.py`、`test_d4_context_experiment.py`、`test_d4_evidence_smoke.py` | 规范化和档案负例、实验预算/失败元数据/评分、安装脚本。 |
| `tests/test_core.py`、`test_measurement_archive.py`、`test_measurement_ranges.py` | 当前 schema 断言与真实历史序列化格式的重建；未放宽历史预期。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/D4_EVIDENCE.md`、`docs/D4_CONTEXT_EXPERIMENT.md`、`docs/DECISION_MATERIALS.md` | 当前接口、兼容、实验结论与使用边界。 |
| `.github/workflows/ci.yml`、`MANIFEST.in` | 安装验收新增 D4，分发包包含两个新脚本。 |
| `docs/reviews/2026-11-06-d4-evidence-context.md`、`docs/reviews/README.md` | 本次审查归档及索引。 |

规模（不含本摘要及索引）：业务代码及脚本 +1885/−87；测试 +1057/−2；文档 +206/−6；配置 +15/−4。行数仅作审阅提示。

## 2. 核心逻辑变化

| 规则 | 修改前 | 修改后与影响 |
| --- | --- | --- |
| `build_structure` / `check_measurement` | 切分与单位检查各自计算管道列；无 HTML 网格 | 统一原文网格，修复转义管道误绑与无外管道支持不一致；全 td 表头须显式提示。 |
| `enrich_chunk` / `split_chunk` | 仅文本及 segments；复制简单表头 | 主体与结构/邻段分别保留原文位置；标题、跨行行组不可拆断，子块主体严格减小；表格是邻段搜索屏障。 |
| `coverage_report` / execution | 测试内检查覆盖，运行无完整诊断 | 调用前核对覆盖和重构；最终叶块再检查；策略与结构进入恢复指纹。 |
| `ground_quote` | 仅原文精确匹配 | 精确优先；白名单标点/空白检索保留原文映射，normalized 永远需复核；不跨展示片段拼接。 |
| `archive` / identity / decision bundle | 结果 0.3 的精确引文契约 | 验证新命中、网格与上下文；旧档案及 dev18 决策材料 ID 不变；新结果增加的元数据不得改变旧格式身份。 |

`--context none` 保持默认；`structure` 和 `neighbors` 是显式选择。显式结构输入即使配合 none 也可改变表格切分，文档已说明。LaTeX 原样定位可用，不做公式等价转换。

## 3. 发现的问题及边界

独立审阅发现并修复：长标题被片段端点截断导致已展示引文无法定位；邻段跳过整表；前导空白及单独表头产生额外块；normalized 主体误报仅上下文；无显式索引的表内单位归档复验不一致；实验异常丢失已返回的 usage/partial 输出。根代理还补齐决策材料的实际展示范围、source_context 关联校验及旧 bundle 身份兼容。

多层表头的不同单位声明可能互相冲突；同列机械检查不等于单位语义消歧。HTML 实体、OCR、公式均不改写。重复引文仍可能需要复核；程序不自动决定邻段与主体是否属于同一科研对象。

实验边界：固定三片段、四策略、每策略一次。简单表的结构上下文补足对象，自足正文无变化，邻段相比结构未新增正确 tuple。合并表的孤立后行压力输入仍有错列；自然分块的 rowspan 原子组会保留前后行，因此不能把压力输入结果直接认定为生产流程回归。精确 tuple 评分受来源支持的命名变体影响，人工解释另列，未改动冻结 gold；人工核对不是专家金标。

## 4. 测试证据

| 本轮检查 | 结果 | 边界 |
| --- | --- | --- |
| 仓库根 `.venv/Scripts/python.exe -X utf8 -m pytest -q -ra --basetemp ../.tmp/d4-final-tests --tb=short` | **1104 passed, 3 skipped** | 三项为本机无法创建符号链接/目录链接；其余离线回归通过。 |
| 真实 dev18 冻结源码生成合成 decision bundle，由 dev19 加载、重新准备旧 run、保存重读 | 通过，原 bundle ID 保持 | 合成兼容检查，非真实裁决。 |
| 三份私有完整转换文本的三策略分块与原文覆盖审计 | 全部无覆盖缺口，结构/坐标可回读 | 没有对整篇执行新模型抽取；覆盖不等于召回率。 |
| 七个合成情景、四臂离线对照 | 完成，记录定位收益和新增歧义/其他对象暴露 | 不用于声称模型质量改善。 |
| `python -m build --no-isolation` | wheel / sdist 通过 | 构建临时目录位于工作区。 |
| 仓库外新 venv，wheel 离线安装、`pip check`、导入检查 | 通过；导入位于独立 site-packages；无 LangExtract | 声明依赖先下载到工作区，再无网络安装。 |
| 独立安装的 demo、D1 和八个验收脚本 | 全部通过；模拟 HTTP 23 次，真实端点 0 | D4 另有3次离线回放和3次内存后端调用，恢复新增调用0。 |
| 发布文件列表、凭据/真实材料扫描、`git diff --check` | 通过 | 私有实验及原始输出均在仓库外；公开样例为合成。 |

早期检查中的问题已保留记录：临时目录权限、安装验收脚本拷贝布局不符、旧当前版本断言，以及本轮确认的代码缺陷；修复后上述最终验证通过。未把首次失败写成通过。

真实端点单列：首轮授权的12次尝试全部 transport_error、无响应/usage；保留原失败记录。经另行授权在联网环境重跑同一冻结12请求，12个 stop 响应，结构可解析，无额外重试。成功响应合计输入21,775、输出6,601、总28,376 tokens；金额以服务商账单为准，未估算。未调用 Qwen。固定原文、gold、模型原始输出、逐项评分和私有核对表均不入库。

CI：本摘要冻结时尚未触发，不能用本地通过替代目标提交的 GitHub Actions 结果。

## 5. 人工审查重点

| 定位 | 重点 |
| --- | --- |
| [structure.py:486](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev19/src/attestextract/structure.py#L486) | 不猜全 td 表头，不重写原文；不明确的元数据对应关系如实记录。 |
| [contextual_chunking.py:63](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev19/src/attestextract/contextual_chunking.py#L63) | 主体与辅助片段的边界、章节/表格屏障和邻段预算。 |
| [evidence.py:41](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev19/src/attestextract/evidence.py#L41) | 原位检索、完整展示 scope、规范化来源映射与公式保护。 |
| [planning.py:50](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev19/src/attestextract/planning.py#L50) | 截断恢复按主体减少，保留角色与原文覆盖。 |
| [d4_context_experiment.py:232](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev19/scripts/d4_context_experiment.py#L232) | 冻结请求、防重复预算、失败元数据及原始响应保存；不能用试点证明总体质量。 |

## 6. 最终建议

- 必须修复：本轮已确认的软件阻断问题均已修复，无剩余已确认阻断项。
- 可选优化：另行设计自然分块的更大样本、重复模型运行与专家核对，验证不同领域的收益及错归属风险；本次不追加付费调用。
- 当前可接受：合入结构、定位及覆盖能力，保留上下文开关；默认 none。现有证据不足以认定扩大上下文的总体收益大于风险，尤其不默认启用邻段。D4 工程验收不替代 D5 全篇召回与科学事实质量验收。
