# 审查摘要：已知表格截断的有限细分与恢复

- 日期：2026-11-13。
- 基准：任务开始时的 `47f6b1b9fefe166a9f22b5e145b54bc440c2d1bb`（dev32）；工作区原先干净。
- 目标：固定标签 `v0.1.0.dev33`；本摘要与本轮代码同提交。包括下列实现、测试和文档；不包括外部用户项目、论文、运行目录或凭据。
- 审查来源：实现者自查；`recovery_test_audit` 独立只读检查恢复、预算、迁移、越界检查及最终发布清单；`entity_architecture_audit` 独立检查窗口责任范围与旧请求兼容。各审阅者编写的测试或展示代码另由实现者/另一审阅者交叉检查。
- 结论：当前可接受；审阅发现的越界观测值、根表身份和损坏元数据类型检查均已修复。未改变科学语义归并，不表示真实论文抽取质量通过。
- 包版本：`0.1.0.dev33`。摘要编写时尚未提交/推送，CI尚未触发；后续发布状态以固定标签和交付回复为准。

## 1. 逐文件职责

| 文件 | 作用与原因 |
| --- | --- |
| `src/attestextract/table_windows.py` | 新增物理行/单元格二分、窗口责任元数据和越界隔离；正文切分器不能承担表格的引用边界。 |
| `src/attestextract/table_window_migration.py` | 新增受限旧检查点桥接，逐一重建请求及输出契约；不能仅放宽版本指纹。 |
| `src/attestextract/document_routing.py` | 识别子窗口并保留原表身份；更新路由兼容修订。 |
| `src/attestextract/execution.py` | 只对明确长度失败表启用二分，验证恢复树、预算及已保存失败。 |
| `src/attestextract/automatic_execution.py` | 将窗口、旧根检查点迁移接入v4自动适配会话。 |
| `src/attestextract/pipeline.py` | 动态加入窗口请求，重放与新回答使用相同隔离规则；不直接重发失败父表。 |
| `src/attestextract/table_routes.py` | 子窗口增加责任范围指令与校验；未拆表请求保持兼容。 |
| `src/attestextract/table_retry.py` | 已有显式结构重试识别子窗口并检查值来源。 |
| `src/attestextract/cli.py` | 离线检查最终叶块及独立表数，更新提示。 |
| `src/attestextract/expert_review.py` | 子窗口候选编号使用chunk身份，避免兄弟窗口相同record_id碰撞。 |
| `src/attestextract/reporting.py` | 分别显示原表数、处理块数、窗口ID及负责的原格。 |
| `src/attestextract/models.py` | 明确TableExtractionResult可以表示原表窗口；不新增结果字段。 |
| `tests/test_table_windows.py`、`tests/test_table_window_adversarial.py` | 二分覆盖、空格/合并格/单格边界、越界锚点和值来源、伪造根表身份。 |
| `tests/test_table_window_recovery.py`、`tests/fixtures/dev32_table_truncated.json` | 用未修改dev32生成的合成冻结运行验证迁移、账本、损坏输入、CLI离线预检。 |
| `tests/test_automatic_table_windows.py` | v4路径归属、预算停止、剩余窗口恢复及结果未知不细分。 |
| `tests/test_table_window_review.py`、`tests/test_table_window_reporting.py` | 候选编号、混合整表/窗口报告与未拆表兼容。 |
| `tests/test_table_entry.py` | 原有禁止切表用例明确改为max_split_depth=0，保留可关闭能力。 |
| `scripts/table_window_smoke.py`、`MANIFEST.in` | 可随源码包运行的独立离线安装验收；不依赖tests模块。 |
| `.github/workflows/ci.yml` | 安装断言从pyproject读取版本，修复遗留dev30硬编码；将窗口smoke纳入CI。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 包版本。 |
| `README.md`、`CHANGELOG.md`、`docs/EXECUTION.md`、`docs/TABLE_EXTRACTION.md` | 说明行为、兼容与边界；不承诺减少输入tokens或必然完整。 |
| 本摘要、`docs/reviews/README.md` | 归档审阅与验证证据。 |

改动规模（Git行数）：业务代码 +397/-34；测试与独立验收脚本 +1198/-4；文档 +136/-9；打包与CI配置 +24/-9。两个新增业务模块分别承担表格空间边界与旧档案验证，不新增依赖；行数仅作审阅提示。

## 2. 关键行为前后

| 规则 | 修改前 | 修改后 |
| --- | --- | --- |
| 表格length失败 | 不能正文式细分；原表已有请求又不能直接重发 | 按物理行二分，单行再按完整单元格二分；两个子窗口各自预留预算。 |
| 上下文与所有权 | 一个原表一个任务 | 完整原表作为共享上下文；每个原格只归一个窗口，禁止借用其他窗口的观测值。 |
| 恢复 | 旧失败表阻断继续 | 验证成功块后复用，只处理剩余窗口；父表失败及既有额度不清零。 |
| 不明传输结果 | 停止、不自动重复 | 保持不变；仅明确length允许表格细分。 |
| 旧身份 | 改路由修订会拒绝旧指纹 | 仅内置后端dev31/dev32单次未拆根块限定桥接，检查请求、Schema、缓存、原文、任务及账本。 |
| 展示 | 原表ID可能重复表示不同处理块 | 候选ID和报告区分窗口；原表ID、原值和证据仍保留。 |

结果schema仍为0.6（含表格），检查点仍为v3固定/v4自动适配，flat-v1不变。初始分块、根表提示词及未拆表报告兼容；新窗口只改变明确截断后的行为。

## 3. 审阅发现与修正

- 仅检查anchors不够：模型可以声明本窗口锚点，却把value来源写成另一个窗口的column/context。已对简便任务value、显式测量值字段及未声明为身份/条件/单位的数值字段实施一致隔离；实时、重放、评估和显式表格重试均接入。
- 伪造窗口可引用另一张表的根ID。已核验窗口根ID与table_id一致。
- 旧document/meta若是JSON数组，原分支可能抛AttributeError。已先检查字典类型，损坏档案明确拒绝。
- 待处理而未失败的表不应直接细分。已要求failed、已有调用且error_code为truncated_response。
- 发布前发现CI仍硬编码dev30，安装新版会误报版本错误。已读取pyproject的期望版本，保留安装结果与发行版本一致性校验。
- 独立复核上述修复未发现残留阻断项。未将当前entity文本分组视为实体归并，未新增CCUS规则。

## 4. 本轮验证

| 实际命令/方式 | 结果与边界 |
| --- | --- |
| `python -m pytest -q --basetemp <本地临时目录> --tb=short` | 最终2129 passed，1 skipped，296.53s；使用本地回环TLS，不调用真实模型。 |
| 恢复/窗口/对抗用例三个文件 | 45 passed，含21项恢复测试；最终全套包含这些用例。 |
| 路由、恢复、本机TLS与provider review integration | 55 passed；用于复核最初全套的失败。 |
| reporting、expert review相关测试 | 37 passed；旧dev32同输入未拆表报告全文相同。 |
| 比较未修改dev32构建的4种根表请求 | payload完全相同；包括简便/自定义任务及两种接口封装。 |
| `python -m build --no-isolation --outdir .build/dev33` | wheel和sdist构建成功。 |
| 干净venv通过本地wheelhouse安装wheel；`pip check` | 安装成功，无损坏依赖；导入来自site-packages，未安装LangExtract。 |
| 安装后从仓库外执行`table_window_smoke.py --output-dir <新目录>` | 源码及wheel均通过：初次2调用、恢复1调用、完成后重放0调用、4条原观测及引用保留、CLI检查零网络。socket/DNS被阻断。 |
| CI内嵌Python编译/AST检查；已安装wheel运行demo和d1 | 语法通过，旧演示结果均报告dev33；远端CI尚未运行。 |
| 私有失败运行离线预检 | 缓存与截断细分可验证；旧文件哈希不变，零网络/新生成。私有材料不提交。 |

首轮在受限沙箱/同步目录全套出现5项失败：1项待处理表细分逻辑已修复，1项同步文件锁，3项本机TLS被沙箱限制。迁移到本地临时目录并允许本机测试后得到上述最终全套结果；不把最初失败写成通过。

真实端点：本轮0次；真实模型是否遵守窗口责任、是否完成整篇及科学含义仍待用户在线验收。CI编写时未触发。包含表结果的prepare-decisions仍明确不支持，不能宣称Jev已接入。

## 5. 重点审阅入口

| 固定版本定位 | 应确认的问题 |
| --- | --- |
| [table_windows.py:77](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev33/src/attestextract/table_windows.py#L77) | 边界必须落在物理行/格之间，单格或深度到限明确停止。 |
| [table_windows.py:144](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev33/src/attestextract/table_windows.py#L144) | 共享对象/单位可以越窗，观测值不可以；不能根据任意字符串名猜角色。 |
| [table_window_migration.py:21](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev33/src/attestextract/table_window_migration.py#L21) | 旧根请求及档案必须可重建，不跟随任意历史路径、不放宽任务身份。 |
| [execution.py:415](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev33/src/attestextract/execution.py#L415) | 已知截断、总预算、深度、父失败和子复用必须同时成立。 |
| [reporting.py:243](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev33/src/attestextract/reporting.py#L243) | 原表与窗口数量、编号不得混淆，不把处理完成显示为科学核验通过。 |

## 6. 结论与剩余边界

- 必须修复：本范围内未发现尚未解决的阻断项。
- 可选优化：未来按实体身份和观测角色建模，支持独立语义验收；不应把某篇地学论文的判断写死在通用引擎中。
- 当前可接受：明确长度截断后的有限细分和可靠恢复。仍首先发送整表，窗口减少输出责任而不减少输入tokens；单格、预算或深度到限仍停止。旧运行若已细分/重生成，或身份不可核实，拒绝桥接。任意自定义字符串字段的语义、实体归属及最终数据库行均不由本修复推断。
