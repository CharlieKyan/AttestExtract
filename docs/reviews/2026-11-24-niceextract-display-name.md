# NiceExtract 品牌拼写与发布准备

审查日期为 2026-11-24。任务开始时工作区干净，基准为 `5b19b8f25b987771beca119c0b7251dd199c6123`。本地发布分支上的改名提交为 `02dfc2901caf4c743babd5bc3672518e4daa837a`。本摘要及索引在随后的本地文档提交中归档。审查来源为实现者自查和独立只读审阅，覆盖品牌入口、异常兼容、下载路径、历史记录和分发元数据。

## 1. 文件与职责

| 文件 | 变更原因 |
| --- | --- |
| `README.md`、`README.zh-CN.md` | 统一首页的品牌拼写，并让试用者从预览分支取得当前源码。 |
| `NOTICE`、`pyproject.toml` | 更新产品署名的首行和包的首页地址；版权人及许可证沿用已有声明。 |
| `AGENTS.md`、`CHANGELOG.md`、`docs/MEASUREMENT_REVIEW.md` | 更新仓库约定标题、迁移说明和示例目录名。 |
| `src/niceextract/__init__.py`、`src/niceextract/errors.py` | 更新对外说明，增加符合新拼写的异常导入名。 |
| `src/niceextract/cli.py`、`src/niceextract/diagnostics.py` | 命令行和诊断使用新导入名捕获原来的异常。 |
| `scripts/d2_acceptance.py`、`scripts/provider_experiment.py`、`scripts/table_route_experiment.py` | 同步三个脚本的异常导入与捕获语句。 |
| `tests/test_diagnostics.py` | 覆盖两种异常拼写的同一性、捕获行为及已保存的诊断类型。 |
| `docs/reviews/2026-11-24-niceextract-display-name.md`、`docs/reviews/README.md` | 归档本轮结论并加入索引。 |

改名提交共涉及 15 个文件。业务代码新增 13 行、删除 9 行，脚本新增和删除各 6 行，测试新增 11 行、删除 1 行，文档新增和删除各 16 行，NOTICE 与包配置新增和删除各 2 行。另有本摘要与索引两份归档文档。

## 2. 接口与兼容性

`niceextract.errors.NiceExtractError` 与原来的 `NICEExtractError` 指向同一类对象。原拼写的导入、子类捕获和直接实例化基类时的诊断类型名继续有效。分发名、Python 导入、CLI 均为 `niceextract`，环境变量前缀为 `NICEEXTRACT_`。历史标签和相应审查摘要记录其当时的名称。

## 3. 发布范围核对

当前候选的跟踪文件包含代码、说明、合成示例及测试夹具，本轮按文件名检查未发现跟踪的 CSV、SQLite 成品、论文 PDF 或真实配置文件 `.env`。代码中存在可选的数据导出功能。初版的材料说明应注明随附的合成 JSON 示例及拟公开范围。依文件名进行的检查不能代替对内容、历史记录及权属的保密审查。

## 4. 实际验证

- 在仓库根目录使用项目 Python 环境运行 `python -m pytest -q --basetemp <仓库外的系统临时目录> --tb=line`，2500 项通过、1 项跳过。最初的沙箱临时路径过长，改用仓库内短路径时又触发了验收工具对输出位置的限制。最终在仓库外的短路径完整运行通过。
- 从受跟踪文件在临时目录运行 `python -m build --no-isolation`，成功构建 `niceextract-0.1.0a1` 的 wheel 与源码包。检查包名、版本、首页及 Apache-2.0 元数据，并核对 wheel 的 NOTICE、LICENSE 和说明及源码包内的双语首页与署名。
- `git diff --check`、两版 README 的本地文件链接及项目名称检查通过。本地验证使用离线测试和合成材料，真实模型调用为 0。本轮本地提交尚未触发远端 CI。

## 5. 人工审阅入口

以下链接绑定改名提交，推送后可从 GitHub 访问。在本地可用同一 SHA 和相对路径核对。

- [README.zh-CN.md 第 1 行](https://github.com/CharlieKyan/AttestExtract/blob/02dfc2901caf4c743babd5bc3672518e4daa837a/README.zh-CN.md#L1) · 产品首页展示名。
- [NOTICE 第 1 行](https://github.com/CharlieKyan/AttestExtract/blob/02dfc2901caf4c743babd5bc3672518e4daa837a/NOTICE#L1) · 分发时的产品名称及版权署名。
- [src/niceextract/errors.py 第 24 行](https://github.com/CharlieKyan/AttestExtract/blob/02dfc2901caf4c743babd5bc3672518e4daa837a/src/niceextract/errors.py#L24) · 新旧异常名是否保留类身份。
- [tests/test_diagnostics.py 第 151 行](https://github.com/CharlieKyan/AttestExtract/blob/02dfc2901caf4c743babd5bc3672518e4daa837a/tests/test_diagnostics.py#L151) · 原诊断类型的回归保护。

## 6. 结论与发布状态

本次改名可形成送审候选。未发现必须修复的品牌或兼容性问题。包版本为 `0.1.0a1`，改名提交保存在本地。既有远端仓库及预览分支此前已公开，默认分支为 `main`。本轮没有新建公开标签或 Release，也没有上传分发包；机构审查状态由资料提交方确认。
