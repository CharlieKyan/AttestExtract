# 首版 GitHub 对外入口

审查基准为原送审候选 `393d973e257f2d3b63c27e4b546e79d7f85b76e0`，任务开始时工作区干净。入口调整提交为 `e06e775800104a6a58962f6e796fbc47bd08c59f`。本摘要和索引随后归档在同一条本地候选分支。原送审分支和附件保留，供院内核对差异。

## 1. 修改范围

| 文件 | 修改内容 |
| --- | --- |
| `README.md`、`README.zh-CN.md` | 快速开始指向计划使用的 NiceExtract 仓库及固定版本源码 ZIP。 |
| `pyproject.toml` | 将项目首页、源码仓库和问题反馈三个地址改为最终仓库路径。 |
| `docs/reviews/2026-11-24-public-entry-urls.md`、`docs/reviews/README.md` | 归档对外入口核对结果，增加索引。 |

入口提交变更 5 行并替换 5 行，其中首页文档 2 行、配置 3 行。业务代码、测试文件均无变更。

## 2. 用户入口变化

获批改名并创建正式标签后，访客可从 `CharlieKyan/NiceExtract` 的默认首页了解项目，并从两种语言的快速开始下载 `v0.1.0a1` 对应的源码。安装名、CLI、Python 接口和 JSON 格式沿用已审候选的实现。

## 3. 需核实的问题

仓库地址和标签在准备时尚未对外启用。旧提交的审查文档继续引用其原有仓库地址和提交。改名后需从 GitHub 实际点击新版下载与问题反馈入口，并抽查历史链接的重定向。[GitHub 仓库更名说明](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository)

## 4. 实际检查

- 用 `tomllib` 读取包配置，两版源码 ZIP 的仓库、版本和路径与包版本 `0.1.0a1` 一致；三个对外元数据地址符合预期。
- 两版首页本地文件链接存在，`git diff --check` 通过。独立只读盘点核对了五处现行入口，均在上述三个文件内。
- 本次只改入口文案和包元数据，未重跑全量 pytest；先前代码版本为 2500 项通过、1 项跳过。最终附件的构建、安装验证和哈希随冻结后的本地送审材料记录。候选远端 CI 留待审批后推送。

## 5. 人工审阅入口

下面的链接固定到入口提交，仓库改名并推送后可在线访问；在本地可用同一 SHA 和行号查看。

- [中文首页第 30 行](https://github.com/CharlieKyan/NiceExtract/blob/e06e775800104a6a58962f6e796fbc47bd08c59f/README.zh-CN.md#L30) · 源码 ZIP 是否绑定最终标签。
- [包配置第 31 行](https://github.com/CharlieKyan/NiceExtract/blob/e06e775800104a6a58962f6e796fbc47bd08c59f/pyproject.toml#L31) · 包元数据能否把访客带到项目默认首页、仓库和问题反馈入口。

## 6. 结论

本地入口调整可用于送审。未发现必须修复的文案或配置问题。是否需要补充院内材料取决于已有批复的覆盖范围。实际改名、远端 CI、默认首页切换、正式标签和 GitHub Pre-release 属于批准后的发布操作。PyPI 分发另行安排。
