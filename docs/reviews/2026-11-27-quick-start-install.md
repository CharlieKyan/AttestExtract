# 从工作目录安装预览版并运行离线演示

审查基准为任务开始时的 `df271eba747b043277a77a433f08dcca9e90dc69`，工作区干净。目标为首页提交 [`b4117da`](https://github.com/CharlieKyan/NiceExtract/commit/b4117da214e75e366ad5e7faf46cd6b470be5622)。本摘要与索引在后续文档提交归档。审查方式为实现者自查与独立的安装和演示核验。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `README.md` 和 `README.zh-CN.md` | 同步安装入口、演示输出位置、本地 wheel 安装方法，以及独立工作目录中的配置与复核示例。 |
| `docs/reviews/2026-11-27-quick-start-install.md` 和 `docs/reviews/README.md` | 记录本次检查，在索引增加入口。 |

业务代码与测试文件均为零改动。两份首页合计新增 24 行、删除 16 行，另有本摘要和一条索引。

## 2. 使用方式变化

读者可以让 pip 直接安装固定 GitHub ZIP，再运行包内演示。此前需要手动解压并进入源码目录。拥有 dev60 wheel 的读者可在新的虚拟环境中从本地文件安装。单篇复核示例先生成输入 JSON，再调用 `export-review`。所有变更均为文档示例。

## 3. 证据支持的问题

更新时 GitHub Releases 列表为空，公开安装入口指向原先可下载的预览快照 `5b19b8f`。预览版和 dev60 候选包都使用 `0.1.0a1` 版本号，单凭版本字符串无法区分二者。首页用固定快照标识公开版本，另行说明已有 wheel 的安装方式。

试运行中，把审阅文件导出到输入 JSON 所在目录的子目录会被命令拒绝。最终示例把单篇结果写入 `demo-run`，将审阅文件写入并列的 `review-example`。

## 4. 实际验证

- 在 Python 3.13.9 临时虚拟环境中，用首页的 `python -m pip install` 命令从 GitHub 下载并成功安装预览快照。验证使用本地缓存的构建依赖，运行依赖复用本机环境。
- 在源码树外运行默认演示，得到 2 条科研候选和 3 条采购候选。随后按首页的 `mkdir demo-run`、单篇 `demo` 和 `export-review` 三条命令操作，生成了可阅读的 `annotation.md` 和可解析的 `annotation.json`。
- 在先前安装 dev60 验收 wheel 的环境中，从新的临时目录执行 `niceextract demo --output result.json`，成功读出 `ccus` 与 `procurement` 两组结果。
- 双语命令块比对通过。配置文件下载链接返回 HTTP 200，两个首页都使用下载后的文件名 `backends.toml`。
- `git diff --check` 通过。此次只修改文档，未运行完整测试或单独构建发行包，未调用模型服务。

## 5. 人工审查重点

- [中文快速开始，第 15–24 行](https://github.com/CharlieKyan/NiceExtract/blob/b4117da214e75e366ad5e7faf46cd6b470be5622/README.zh-CN.md#L15-L24) 区分公开 ZIP 与本地 dev60 wheel，说明输出位置。
- [英文配置示例，第 161–165 行](https://github.com/CharlieKyan/NiceExtract/blob/b4117da214e75e366ad5e7faf46cd6b470be5622/README.md#L161-L165) 使用读者保存在工作目录中的 TOML 文件。
- [中文单篇复核，第 198–206 行](https://github.com/CharlieKyan/NiceExtract/blob/b4117da214e75e366ad5e7faf46cd6b470be5622/README.zh-CN.md#L198-L206) 使用单篇演示生成的 JSON 作为导出输入。

## 6. 结论

当前首页可接受，未发现必须修复项。dev60 wheel 公开后可用实际附件直链简化安装步骤。本次交付限于文档和审查记录。
