# 最少输入的 API 抽取

适用版本：`0.1.0.dev25`。普通用户提供 API **基础地址**、Key、服务原始模型名、任务和原文。地址是服务文档给出的 API 前缀，例如 `https://service.example/v1`，不是完整的 `/chat/completions` 操作地址。

首次调用直接抽取原文第一块。没有注册、profile 导入或三次合成测试的必经步骤。当前通用路径支持 Bearer 认证、非流式的兼容 Chat 接口；其他协议需要相应适配器，不能承诺任意 HTTP API 自动适配。任务仍为现有 TaskSpec/JSON，不是自动生成的自然语言任务。

## 普通 CLI

以下是安装后可以直接粘贴的 PowerShell 命令。按提示输入自己的地址、模型以及已有任务、原文文件路径。密钥已存在于 `ATTESTEXTRACT_API_KEY` 时直接使用；否则交互终端会隐藏输入。不要把真实密钥写进命令行参数或配置文件。

```powershell
$extractApiBase = Read-Host 'API 基础地址（例如 https://service.example/v1）'
$extractModelName = Read-Host '服务原始模型名'
$extractTaskPath = Read-Host '任务 JSON 文件完整路径'
$extractSourcePath = Read-Host '原文 TXT 或 Markdown 文件完整路径'

python -X utf8 -m attestextract extract `
    --base-url $extractApiBase `
    --model $extractModelName `
    --task $extractTaskPath `
    --input $extractSourcePath

if ($LASTEXITCODE -ne 0) { throw '抽取未完成，请保留终端显示的诊断目录和恢复命令。' }
```

不需要填写 TLS、输出协议、温度或 token 参数名称。默认读取超时 180 秒，保持证书验证；默认每次生成一个 HTTP POST。输出、报告和诊断默认保存到输入附近的 `outputs` 目录。非交互环境必须提供密钥环境变量；无法保证隐藏输入时明确停止，不回显密钥。

追加 `--check` 可离线查看分块、模式与预算，零网络调用，不验证密钥、余额或服务能力。`--structured-output off|json_object|json_schema`、明确 profile/adapter 仍选择旧固定路径；`--structured-output auto` 明确选择本页的自动路径。旧 `--model-id provider:model` 保留预设语义。匿名本地接口可用显式固定模式。

## Python

```python
import getpass
import os
import warnings
from pathlib import Path
from attestextract import extract, load_task

api_base = input("API 基础地址: ").strip()
model_name = input("服务原始模型名: ").strip()
task_path = input("任务 JSON 文件路径: ").strip()
source_path = input("原文文件路径: ").strip()
api_key = os.environ.get("ATTESTEXTRACT_API_KEY")
if api_key is None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        api_key = getpass.getpass("API Key: ")

result = extract(
    Path(source_path).read_text(encoding="utf-8-sig"),
    load_task(task_path),
    base_url=api_base,
    api_key=api_key,
    model=model_name,
)
Path("result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
```

Python 内存调用也受调用预算限制，但恢复需要持久化 `RunTrace`。现有 `trace=`、`execution=ExecutionOptions(...)`、`resume_from=` 接口继续有效；CLI 默认保存诊断，因此适合直接验证恢复。高级 `model_options` 保留显式参数覆盖能力。

备用模型可通过 Python `fallback=ModelConnection(base_url=..., model=..., api_key=...)` 选择。CLI 使用全部三个可选参数 `--fallback-base-url`、`--fallback-model`、`--fallback-api-key-env`；备用必须绑定自己的凭据变量。框架不会搜索其他账户或擅自选择供应商。不需要备用时不填。

## 自动行为与边界

已知地址与具体型号联合匹配参数：DeepSeek Flash Chat 使用 Object/关闭思考，Qwen 北京已知型号使用 Object/非思考；Wenwen Gemini 2.5 Flash 与 GPT-4.1 使用 Chat Schema，不额外发送温度。DeepSeek 的已验证 Responses 路径可以成为同模型替代；不猜测 Wenwen 或陌生服务的 Responses 接口。

未知兼容服务先请求 Schema。只有明确的输出模式不支持错误才允许 Schema→Object→提示词 JSON；明确 token 字段拒绝才转换字段名称，不删除输出上限。普通 400、损坏 JSON 不会触发这种降级。所有模式仍通过相同的本地字段/类型/证据检查。

- 自动路径默认每块最多三次生成、全篇初始块数 N＋2 次；`--max-model-calls` 可覆盖总上限，已经消耗的次数不退还。
- 完整响应格式失败，可使用已验证的同模型替代或显式备用；`--format-retries 1` 允许一次同路径重生成，仍共享上述预算。
- 超时、写入/读取断线、未知协议断开都停止；不因备用存在而自动再次付费请求。
- 认证、权限、证书问题停止。额度/模型/服务不可用仅在有显式备用时切换。
- 截断使用已有细分流程并共享预算；证据警告、合法空列表或事实歧义不触发自动换模型。
- 非敏感成功参数以摘要缓存到用户 `.attestextract/cache`；`ATTESTEXTRACT_MODEL_CACHE_DIR` 可指定独立缓存目录。缓存不包含密钥，也不记录论文。缓存只是曾成功使用的偏好，不保证当前服务或任务质量。
- 自动运行使用 result 0.5/checkpoint v4。每块模型来源和每次调用单独保存；旧固定运行继续使用旧格式。v4 恢复使用冻结路径，不受后来缓存偏好影响。旧检查点不能直接加备用模型。

密钥、服务地址、模型、接口能力、格式正确性和科研内容正确性分别检查。已保存参数不意味着服务不会停机、余额不会变化，或模型不会产生格式和事实错误。

## 新供应商的独立验收

先固定已安装版本，在干净环境和空缓存中，选择此前没有参与开发的兼容服务。以下完整命令复制包内三个合成任务，然后使用普通 `extract` 入口运行；默认每任务最多三次、总计最多九次生成，输出上限每次 2,048。失败即停止，不补跑。费用以服务商账单为准。

这段命令使用独立的 `ATTESTEXTRACT_ACCEPTANCE_API_KEY` 凭据变量；未设置时每次隐藏输入，不会读取默认账户的 Key。若设置该变量，请确认它属于本次输入的服务地址。

这段命令会真实调用用户提供的服务。它是开发者/高级用户可选验收，不是普通抽取的前置条件。不能把已有供应商的新密钥当成新的独立供应商验证。

```powershell
$acceptApiBase = Read-Host '此前未适配的兼容 API 基础地址'
$acceptModelName = Read-Host '该服务的模型名'
$acceptRoot = Join-Path (Get-Location) ('api-acceptance-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $acceptRoot -ErrorAction Stop | Out-Null
$priorCacheDirectory = $env:ATTESTEXTRACT_MODEL_CACHE_DIR
$env:ATTESTEXTRACT_MODEL_CACHE_DIR = Join-Path $acceptRoot 'empty-model-cache'

try {
    @'
import sys
from importlib.resources import files
from pathlib import Path
import attestextract
root = Path(sys.argv[1])
print('Installed version:', attestextract.__version__)
for name in ('attribute', 'observation', 'relation'):
    sample = files('attestextract').joinpath('examples', 'd1', name)
    target = root / 'inputs' / name
    target.mkdir(parents=True)
    for filename in ('source.txt', 'task.json'):
        (target / filename).write_bytes(sample.joinpath(filename).read_bytes())
'@ | python -X utf8 - $acceptRoot
    if ($LASTEXITCODE -ne 0) { throw '未能准备安装包内的合成材料。' }

    foreach ($acceptSample in @('attribute', 'observation', 'relation')) {
        Write-Host "正在验收：$acceptSample"
        python -X utf8 -m attestextract extract `
            --base-url $acceptApiBase `
            --model $acceptModelName `
            --api-key-env ATTESTEXTRACT_ACCEPTANCE_API_KEY `
            --input "$acceptRoot\inputs\$acceptSample\source.txt" `
            --task "$acceptRoot\inputs\$acceptSample\task.json" `
            --max-output-tokens 2048 `
            --max-model-calls 3 `
            --max-split-depth 0
        if ($LASTEXITCODE -ne 0) { throw "验收停止：$acceptSample。保留失败目录，不从头补跑。" }
    }
}
finally {
    $env:ATTESTEXTRACT_MODEL_CACHE_DIR = $priorCacheDirectory
}
```

查看各任务的 `result.json`、`report.md`、`diagnostics/execution/checkpoint.json` 和每次原始回答。期望值来自包内合成样例，不能用当前模型回答重新定义。三次任务成功不等于整篇或跨领域质量通过；若为此新服务临时修改了代码，应保留原失败并重新冻结版本，不能冒称原版本直接兼容。
