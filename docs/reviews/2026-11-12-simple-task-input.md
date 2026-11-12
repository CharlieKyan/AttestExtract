# 审查摘要：简便任务入口与观测输入

- 审查日期：2026-11-12。
- 比较基准：`2ca15e9ab44c33e6906a931b6932b16281bdac44`，任务开始时 dev28 HEAD；工作区起初干净。
- 审查目标：`v0.1.0.dev29`；摘要与实现同一版本提交，链接绑定该固定标签。
- 范围：本轮通用内核、合成测试、安装验收和使用文档。私有论文、任务材料、专家批注、原始输出、凭据及日志均不在公开提交中。
- 审查方式：实现者自查；未参与实现的独立代理只读审阅完整内核差异，另由实现代理交叉检查集成入口。独立测试200通过、3跳过，未发现阻断问题。
- 总结论：独立审阅、完整回归、干净安装及公开文件隐私检查通过，可按授权发布。
- 版本：`0.1.0.dev29`；本摘要记录发布前验收，提交、推送与对应托管 CI 状态另行报告，不预报发布成功。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/task_input.py`、`models.py` | 离线编译完整目标清单，定义示例与输入快照；复用原严格JSON解析和保守引文定位。 |
| `src/attestextract/pipeline.py`、`cli.py` | Python关键词与同一个CLI任务文件入口；任务检查先于模型准备；保留旧TaskSpec。 |
| `src/attestextract/prompting.py`、`table_routes.py` | 正文格式示例和表格语义示例分开；不复制快照到模型任务中；表格缺值检查与正文一致。 |
| `src/attestextract/document_routing.py` | 更新表格兼容修订，防止检查行为变化后静默复用旧表检查点。 |
| `src/attestextract/progress.py`、`reporting.py` | 显示完整目标与示例数量，区分有值候选、缺值候选和零候选目标。 |
| `src/attestextract/examples/observations.task.json`、`observations.txt` | 公共合成任务与原文，演示未被示例覆盖的目标仍可抽取。 |
| `tests/test_task_input.py`、`test_simple_task_entry.py`、`test_simple_task_prompts.py` | 输入错误、协议隔离、旧提示词兼容、普通及自动路径、恢复、缺值与报告回归。 |
| `tests/test_cli.py` | 缺模型配置测试改用有效旧任务作为前置条件；保留原有缺配置错误断言，适应任务校验前移。 |
| `scripts/simple_task_smoke.py`、`tests/test_simple_task_smoke.py` | 阻断网络的安装包公开入口验收，包含正文、表格、CLI及恢复。 |
| `.github/workflows/ci.yml`、`MANIFEST.in` | 源码包收录验收脚本；CI在仓库外执行独立wheel验收并检查模块实际位置。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 同步开发版本。 |
| `docs/SIMPLE_TASKS.md`、`README.md`、`README.zh-CN.md`、`CHANGELOG.md`、本摘要、摘要索引 | 普通用户操作、范围与兼容限制、发布变更及审查证据。 |

改动规模：业务及验收脚本代码新增566行、删除20行；测试新增671行、删除1行；文档新增207行、删除3行；配置及合成样例新增37行、删除8行。行数仅作为审阅提示。

## 2. 核心逻辑变化

| 入口/规则 | 修改前 | 修改后与影响 |
| --- | --- | --- |
| `extract` / `load_task` / CLI | 普通用户必须提供内部TaskSpec。 | 可用说明、字段定义和可选示例；框架自动编译，准备模型前发现输入错误。 |
| `compile_task` | 没有业务清单入口。 | 一目标一观测类型；四项字符串字段，只有value业务必填；示例不增删范围，不调用模型规划任务。 |
| 示例证据 | 只有内部自动格式样例。 | 用户原文与标注独立保存；引文无法定位报错，保守规范化/歧义给warning，加载已编译快照不重复warning。 |
| 快照与指纹 | TaskSpec没有用户示例。 | 新输入及编译修订进入既有任务指纹；旧TaskSpec无新增序列化键；更改示例不能复用缓存。 |
| 提示词 | 正文与表格各有独立契约。 | 保留两种契约；正文转换对应格式，表格只用语义示例，不制造表格锚点或scope。 |
| 表格必填值 | 只将null判为缺失。 | 空字符串和纯空白也标缺失，但不删除原值和已有证据。 |
| 报告范围 | 仅查看实际返回候选。 | 简便任务列出全部目标；有值不等于有效，零候选不等于原文未报告。 |

## 3. 潜在问题与边界

- 独立审阅未发现本轮内核阻断缺陷。输入结构与引文匹配检查不能证明示例标注语义正确。
- 编译不从自然语言补充遗漏字段；字段清单必须完整。范围和均值保留原文字符串，不自动计算，也不新增嵌套数值类型。
- 表格字符串字段没有因此获得额外测量组检查；未执行的检查仍为not_applicable/not_run，不报告为科学正确。
- `dev29-table-a-2`拒绝直接恢复旧表路由检查点；历史结果仍可读取，旧正文任务指纹及提示词字节不变。
- 混合正文/表格结果仍不能交给正文专用接受档案或决策包；未接入真实Jev。
- Windows过长测试目录会触及现有路径限制；本轮验收使用工作区内短路径。未以此为由改写原文或扩大产品范围。

## 4. 测试证据

| 本轮实际检查 | 结果 | 边界 |
| --- | --- | --- |
| 独立代理：task_input、simple_task_entry、simple_task_prompts、core、table_entry_cli、execution、decision_bundle | 200 passed，3 skipped | 合成及离线回归，未验证真实端点或科研正确性。 |
| 新任务编译及入口 | 63 passed；后补陌生服务自动路径专项1 passed | 无效输入先于凭据；普通与自动恢复身份绑定输入，超时设置变更可复用。 |
| 旧提示词与表格路由/公开入口 | 62 passed及9 passed | dev28旧flat/native/table提示词基准哈希相同，表格缺值保真。 |
| 新安装验收脚本源环境执行 | 1 passed | 4次合成后端生成，0网络/真实模型调用。 |
| 私有四篇原文公开CLI `--check` | 全部退出0，完整字段及原文覆盖检查通过 | 零联网；表头/标题定位提示保留，不能视为线上成功或质量通过。 |
| 全量回归 | 1969 passed，1 skipped，279.20秒 | 项目Python执行`python -X utf8 -m pytest -q --basetemp <唯一普通本机TEMP目录> --tb=short --junitxml <私有验收目录>`；回环TLS允许连接，未连接模型服务。跳过原因：Windows账号无法创建符号链接。 |
| wheel与sdist构建、干净安装 | 构建成功；Python3.13独立venv使用本地wheelhouse安装，simple/table公开入口脚本均通过 | 仓库外运行，模块确在site-packages；无LangExtract；每脚本4次合成后端调用，0网络/真实调用。 |

默认沙箱系统TEMP曾导致pytest setup及构建权限错误；改用工作区临时目录，不改变业务预期。全量初轮1963通过、3跳过、4失败：旧missing-model测试的无效任务先被新校验拒绝；另3项本机TLS因沙箱网络限制失败。修正测试前置任务后，在允许回环连接的环境运行该项与TLS文件共5项通过。未删除或放宽TLS断言。开发中的新测试夹具按实际公共契约修正，并在后续回归中重验。

第二次全量1961通过、1跳过、8项冻结实验校验失败：运行期间清理了`task_input.py`末尾空行。逐文件哈希对照初始wheel确认仅该文件空白变化，严格运行身份校验正确拒绝复用。未修改保护规则；冻结最终代码后重新进行完整回归。

固定源码后的工作区全量1968通过、1跳过、1失败：模拟整篇测试在OneDrive工作区替换诊断文件时收到Windows拒绝访问，未涉及模型调用。保持代码和断言不变，将临时文件移到普通本机TEMP复验。最终wheel另在全新venv重新安装，仓库外简便入口验收通过，零网络；较早安装已覆盖既有表格入口和四篇材料预检。

最终普通TEMP全量通过；wheel内51个Python文件与发布源码逐字节相同。独立只读发布检查再次核查27个公开文件，没有私有论文、专家批注、真实模型回答或凭据值。

真实端点调用：本轮0。私有线上验收命令由用户执行；没有完整专家gold，不报告整篇准确率或召回率。CI状态以最终提交对应的运行记录为准。

## 5. 人工审查重点

1. [任务编译与互斥入口](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev29/src/attestextract/task_input.py#L143)：字段范围来自清单，示例不能缩小范围。
2. [旧序列化与新快照](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev29/src/attestextract/models.py#L102)：旧指纹不变，新输入篡改不能绕过一致性校验。
3. [公开Python入口](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev29/src/attestextract/pipeline.py#L622)：输入错误在模型与凭据准备前停止。
4. [正文示例](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev29/src/attestextract/prompting.py#L163)：示例与实际原文隔离；表格走独立语义指导。
5. [表格缺值检查](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev29/src/attestextract/table_routes.py#L477)：保留空白原值和证据，不把空值当完整观测。

## 6. 最终建议

- 必须修复：未发现未解决的发布阻断项；完整回归与安装验收已完成。
- 可选优化：后续可单独评估任务标注语义检查、表头解释和最终实体归并，不混入本轮。
- 当前可接受：通用观测任务的低门槛输入、可追溯候选与有界恢复；不宣称自动科学核验或任意任务schema推断。
