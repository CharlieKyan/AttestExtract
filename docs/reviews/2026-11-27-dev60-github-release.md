# NiceExtract 0.1.0a1 首发核对

本次整合以任务开始时的公开主分支 `0fde66aa88d09410833737d560b54743d02561ad` 为基准，工作区当时干净。发行源码为 [`73a5e830`](https://github.com/CharlieKyan/NiceExtract/tree/73a5e830ffc4e36382ae2a248329ca771673b432)，核对的 Git 文件树为 `06ef1a450d61a9f2e5679309d57f3d3c01a24408`。首发首页、标志及审查记录在默认分支维护。检查来源为发布执行者自查、独立的分发文件核对与独立的双语示例核验。

## 1. 文件职责

- 获审提交中的源代码、测试、CI 配置、`LICENSE`、`NOTICE` 和其余分发文件原样集成。该版本的功能说明见 [dev60 候选范围](2026-11-27-dev60-release-scope.md)。
- `README.md` 和 `README.zh-CN.md` 将安装入口切换到发行 wheel，列出本地文件安装方式，并从固定版本链接到指南、示例及许可证。
- 保留公开首页原有的 `docs/assets/niceextract-logo.png`、`2026-11-27-prerelease-homepage.md` 与 `2026-11-27-quick-start-install.md` 两份审查记录。本摘要和同目录的 `README.md` 索引说明首发的核对依据。

## 2. 行为与规模

相对获审候选，本次整合修改业务代码 0 行、测试 0 行。相对原公开首页，两份 README 共新增 56 行、删除 66 行。首次从主分支使用者的角度看，安装名、命令和导入名统一为 `niceextract`，dev60 的字段准备、组表与逐值证据可在发行版本使用。

## 3. 核对发现

先前公开预览与本次发行使用相同的 Python 包版本字符串。首页指向带固定标签和文件名的 wheel，使用新的 Python 虚拟环境可避免沿用预览安装。主分支包含上线后的首页与标志，标签对应获审的源码树，二者的使用说明可能有所不同。

## 4. 验证记录

- 重新计算发行包 SHA-256，核对了源码 ZIP 与获审提交中 434 个文件各自的 Git 对象。wheel 哈希为 `e061c046718a3abb25d079cb6c10d14a074df16a2e0ecd26fc3306b938545a3b`，源码分发包为 `63b1412bea8f1173881a85c18b8a69fd9a6423dc0f945463031c96a096674679`。
- 核对双语首页链接引用的 19 个项目文件及相关锚点。用获审 wheel 在独立临时目录运行双语任务示例、`prepare-task`、离线演示和单篇复核导出。本次演示均使用合成材料，外部模型调用为 0。
- 历史候选验收在 Windows 上得到 3147 项通过、1 项因符号链接权限跳过。合并前的托管流水线另行运行 Windows/Linux 与 Python 3.11/3.13 四个组合，各次作业的结论见 [GitHub Actions](https://github.com/CharlieKyan/NiceExtract/actions/runs/45989418550)。本记录编写于 PR 整合阶段。

## 5. 人工重点

- [发行元数据和许可证](https://github.com/CharlieKyan/NiceExtract/blob/73a5e830ffc4e36382ae2a248329ca771673b432/pyproject.toml#L5-L14) 标识版本、Python 要求和公开许可。
- [快速开始](https://github.com/CharlieKyan/NiceExtract/blob/163377da69af80b2ae8c20f1c2c972bfeb68ba84/README.zh-CN.md#L15-L24) 给出安装入口和当前目录下的结果文件。
- [单篇演示与复核](https://github.com/CharlieKyan/NiceExtract/blob/163377da69af80b2ae8c20f1c2c972bfeb68ba84/README.zh-CN.md#L194-L201) 使用独立输入与输出目录，读者可核对报告中的原值和引文。

## 6. 结论

本地文件与示例核对可接受，未发现需要修改获审运行时代码之处。首发使用 GitHub Pre-release 分发 wheel、源码分发包和 SHA-256 清单。发布状态与下载入口见 [v0.1.0a1](https://github.com/CharlieKyan/NiceExtract/releases/tag/v0.1.0a1)。
