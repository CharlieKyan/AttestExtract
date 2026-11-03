# 审查摘要：结果版本元数据修正

- 审查日期：2026-11-03。
- 比较基准：`26bc0450d3d754427f2a62fb37adcc0f054c0be4`（已发布 dev13）；开始时工作区干净。
- 审查目标：`v0.1.0.dev14`；本摘要和修正同提交，保留 dev13 原提交及标签。
- 范围：结果默认版本、相关回归、包／CI 版本与文档；不改变分块和重生成策略。
- 审查来源：实现者自查及 `recovery_acceptance` 独立只读审阅；后者额外验证干净进程导入和历史嵌套归档重读。
- 结论：已确认遗漏并修复；新结果读取当前包版本，历史显式版本保持原样。
- 发布：dev14 按授权普通提交／注释标签／推送；摘要编写时目标 CI 待触发，最终状态见交付回复。

## 1. 变更概览

| 文件 | 职责与修改原因 |
| --- | --- |
| `src/attestextract/models.py` | `ExtractionResult.package_version` 使用延迟默认工厂，消除第二处硬编码版本 |
| `tests/test_version_metadata.py` | 对全部 demo/D1 样例核对版本，区分运行时默认和显式历史版本 |
| `src/attestextract/__init__.py`、`pyproject.toml` | 新版 dev14，不移动已发布 dev13 标签 |
| `.github/workflows/ci.yml` | 三处既有结果／包版本断言更新为 dev14，未删除或放宽检查 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md` | 标明当前版本及此次元数据修正 |
| 本摘要、`docs/reviews/README.md` | 归档缺陷、真实 CI 失败和修复证据 |

改动规模（相对 dev13）：业务代码 +8/-2；测试 +29/-0；文档 +71/-2；打包／CI +4/-4，共 10 个文件。

## 2. 核心逻辑变化

`ExtractionResult` 原来默认写入字符串 `0.1.0.dev12`，发布 dev13 时只更新了包版本及安装断言，遗漏结果默认值。本版通过 `Field(default_factory=_package_version)` 在创建结果时读取 `attestextract.__version__`。延迟读取避免包初始化期间导入模型产生循环；读取旧结果时若有显式版本，不调用默认工厂、不改写历史。结果 schema、候选身份和证据规则不变。

## 3. 潜在问题

- 已确认：dev13 的四个 CI 环境都通过测试与构建，但在仓库外 demo 的结果版本断言失败。[失败 CI](https://github.com/CharlieKyan/AttestExtract/actions/runs/41592999024)。根因是模型默认值硬编码，不是平台行为；本地此前安装验证只核对导入版本和候选数量，未覆盖结果版本，不能替代这个断言。
- 已修复：使用运行时唯一包版本；新增 demo/D1 结果级检查，本地干净安装也执行与 CI 相同的结果版本断言。
- 已检查：干净进程直接导入模型无循环；历史 dev12 结果和嵌套归档重读后版本不变。默认工厂仅在缺少该字段时运行。
- 限制：历史显式错误元数据不会自动被改写；需要重新生成才得到新版本字段。dev13 记录仍可读取。

## 4. 测试证据

| 本轮实际检查 | 结果及边界 |
| --- | --- |
| `.venv\Scripts\python.exe -m pytest -q --basetemp ..\.local-temp\pytest-dev14-full1 --tb=short` | 727 passed，1 skipped（目录链接权限）；该链接项在同任务 dev13 阶段另以普通 Windows 权限运行，1 passed，相关逻辑此后未改 |
| 独立只读审阅：版本测试与全新进程／历史嵌套归档探针 | 2 项测试通过；新结果 dev14，历史结果 dev12 原样保留 |
| 工作区 TEMP/TMP 下 `python -m build --no-isolation` | wheel、sdist 构建通过 |
| 仓库外新 venv 安装 dev14，`pip check`、demo/D1 结果版本断言及四组脚本 | 全部通过；D2／模型切换／截断恢复／格式恢复分别 3／7／4／5 次 Mock HTTP，导入路径为新环境 site-packages；真实 0 |
| `git diff --check`、文件列表与包文件检查 | 无私有研究内容／凭据；最终 sdist 含本摘要 |

真实调用：0。模型效果未验证；本修复不改变请求或输出格式策略。dev13 CI 失败记录保留；dev14 的 Windows/Linux、Python 3.11/3.13 CI 状态以目标提交为准。

## 5. 人工审查重点

| 固定标签定位 | 判断 |
| --- | --- |
| [`models.py:230`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev14/src/attestextract/models.py#L230) | 延迟读取当前版本，避免循环初始化 |
| [`tests/test_version_metadata.py:10`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev14/tests/test_version_metadata.py#L10) | 所有 demo/D1 结果版本与显式历史版本都有独立断言 |
| [`ci.yml:60`](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev14/.github/workflows/ci.yml#L60) | 安装验收保留正确版本要求，不能通过放宽检查掩盖遗漏 |

## 6. 最终建议

- 必须修复：版本硬编码遗漏已修复并增加回归。
- 可选优化：无此次必要范围之外的改动。
- 当前可接受：使用 dev14 作为本任务最终交付版本；保留 dev13 发布历史，分别报告本地验收和目标 CI。
