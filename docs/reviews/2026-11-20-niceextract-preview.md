# NICEExtract 名称迁移与中英文首页预览

基准为发布分支上的 `5135e196d81783061e8a7c4f66fde1bb82750c86`，开始时索引与工作区干净。审查范围为本次代码、打包、示例、文档、测试和验收脚本，目标为固定预览标签 `v0.1.0a1-preview.1`，包版本为 `0.1.0a1`。按用户选择，工作交付到发布分支供预览。实现者自查，并由独立只读审阅者核查凭据选择、脱敏时机、历史缓存、恢复身份和 JSON 契约。

## 文件与职责

逐文件差异见[本次比较](https://github.com/CharlieKyan/AttestExtract/compare/5135e196d81783061e8a7c4f66fde1bb82750c86...v0.1.0a1-preview.1)。批量改名的文件按相同职责合列。

| 文件 | 修改原因 |
| --- | --- |
| `README.md`、`README.zh-CN.md`、`examples/ccus-result.json` | 以两个温度下的同一压力展示结果，链接合成原文及实际回放 JSON，提供安装和使用自有材料的短路径。 |
| `pyproject.toml`、`MANIFEST.in`、`NOTICE` | 统一项目名、入口、版本、包资源和署名；源码包收录首页示例。版权人为 Yuzhi Kuang。 |
| `src/attestextract/` → `src/niceextract/` | 包含 62 个 Python 模块及原有合成资源。大部分文件只迁移路径。更新内置资源查找、入口、异常名、报告标题及内置后端识别。 |
| `envconfig.py`、`config.py`、`automatic.py`、`cli.py`、`pipeline.py`、`diagnostics.py`（均在新包内） | 选择连接命名空间，并在诊断持久化前登记配置中已知的凭据。自动路由缓存可通过显式目录复用。 |
| `backends.py`、`responses_backend.py` | 新发出的结构化模型请求使用 `niceextract_records`；返回的记录结构沿用既有协议。 |
| `.env.example`、`.gitignore`、`examples/README.md`、当前操作指南、`AGENTS.md`、`CHANGELOG.md` | 面向当前版本的命令、示例路径、变量及迁移说明。保留旧运行目录的忽略规则。发布历史及原有审查记录对应其原始版本。 |
| `tests/`、`scripts/`、`.github/workflows/ci.yml` | 用新包名验证原有行为；增加新旧连接设置隔离和诊断脱敏回归；验证实际安装的 CLI、wheel 及虚拟环境内的模块路径。冻结输入保持原始字节。 |
| 本摘要、`docs/reviews/README.md` | 记录变更、证据及交付位置。 |

按 Git 重命名检测计数，源码与随包资源 +135/−80，测试 +769/−608，验收脚本 +217/−217，用户文档与仓库说明 +159/−902（另有本摘要及索引）。公开示例 JSON 为 353 行，其他构建、CI 与配置 +48/−37。多数 Python 文件的变化来自名称和导入路径迁移。

## 行为与迁移

发行名、Python 导入、`python -m` 入口及控制台命令均为 `niceextract`。依赖旧导入名的脚本需修改导入语句。自定义连接推荐同时更新 `BASE_URL`、`MODEL` 和 `API_KEY` 的前缀。仅有旧连接变量时可继续使用；出现新版连接变量时，在新版命名空间内完成解析。显式 `--api-key-env` 按指定变量读取。旧默认路由偏好缓存不会自动复制到新目录，旧显式缓存目录变量仍有效。

显式 `--output` 交付的科研 JSON 保留原文字面值和字符偏移。正文引文、任务示例和表格原始回答可能出现在使用者自己的结果里。首页提供的公开文件取自合成回放。

## 审阅发现及处置

- 旧 CLI 在解析显式 dotenv 和 profile 之前就写入任务诊断。新旧配置共存时，仅登记一个凭据可能漏遮蔽。现在先登记已知凭据，再保存任务诊断；CLI 报告、检查计划和错误信息沿用诊断脱敏。回归同时核对独立结果文件的值、引文和来源哈希。
- 在基准提交的 Windows CI 中，安装后验收的路径比较在模块报告绝对长路径时失败。CI 现对已存在的环境根与报告路径规范化后再验证包含关系；本地实际验证 Windows 长短路径和外部路径拒绝。
- 本轮中间实现的 Python 可选模型入口出现失效的环境文件变量引用。已修复参数传递，并覆盖失败诊断与正常模型选择。
- CLI 提前解析 profile 后，两种互斥选项组合会先报 profile 缺失。已恢复先检查 `--model-id` 互斥选项的顺序。独立定向复验 8 项通过。

## 验证范围

- 代码检查包括最终差异空白检查、跟踪文件清单及常见凭据格式检查。唯一匹配的 PEM 是原有回环 TLS 合成夹具。检查范围内未引入论文、真实配置或运行日志。
- 专项测试包括 142 项品牌与历史恢复测试，及最后修正后的 45 项诊断与连接测试。实际从基准代码生成两个合成 dev41 检查点，固定与自动后端均在新代码上完成恢复，后续 HTTP 请求为 0，历史文件哈希一致。
- 在 Python 3.13.11 构建 sdist，并从 sdist 构建 wheel。全新环境离线安装，`pip check` 及已安装 CLI 版本检查通过。比对包内源码、示例、LICENSE、NOTICE、两份包的许可元数据和首页链接。CLI 合成回放复现公开的两个 12 bar 观测，温度为 25 °C 和 50 °C；除执行耗时外，输出与公开 JSON 一致。
- 安装后简便任务验收通过，报告 2 条正文观测、1 条表格观测，完成后的恢复无新增请求。最少输入验收包含 15 次模拟 HTTP。真实模型端点调用为 0。
- 首轮完整测试因私有实验临时目录位于签出目录内、沙箱回环权限及文件操作失败而未通过。中途验证发现上述变量与选项优先级问题；实现更新期间冻结实验的代码一致性检查亦拒绝了变更。最终在仓库根目录运行项目环境的 `python -m pytest -q`，以仓库外唯一目录作为 `--basetemp`，使用本机回环：2499 项通过，1 项因 Windows 账户不能创建符号链接而跳过，耗时 276.46 秒。
- 推送后的托管 CI 状态以 GitHub 对应提交的 Actions 记录为准。

## 人工重点

1. [中文首页的示例](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0a1-preview.1/README.zh-CN.md#L7)及其两个可直接打开的文件是否方便研究者核对数据。
2. [包元数据](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0a1-preview.1/pyproject.toml#L6)和[控制台入口](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0a1-preview.1/pyproject.toml#L28)是否与实际安装命令一致。
3. [连接命名空间选择](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0a1-preview.1/src/niceextract/envconfig.py#L27)是否隔离新地址与旧凭据。
4. [诊断凭据登记](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0a1-preview.1/src/niceextract/cli.py#L494)是否先于任务落盘。
5. [CI 安装路径核对](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0a1-preview.1/.github/workflows/ci.yml#L98)是否仍能拒绝外部模块。

## 结论

本次预览交付研究者首页和新名称下的 JSON 抽取工具。已修复本轮审阅发现的问题；本地验收范围内未见遗留阻断项。后续是否将预览切换为默认仓库首页由用户审阅后决定。
