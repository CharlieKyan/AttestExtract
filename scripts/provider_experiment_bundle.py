"""Freeze a bounded provider experiment; preparation and checking never call APIs.

Source papers, endpoint addresses and complete prompts belong only in a private
bundle. Credentials and raw proxy settings are never serialized. Checksums
detect accidental changes; they do not authenticate an untrusted bundle.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib.resources import files
import hashlib
import json
import os
from pathlib import Path
import platform
import ssl
import subprocess
from urllib.request import getproxies

import httpx
import httpcore
import certifi
import pydantic

import attestextract
from attestextract.config import BackendConfig
from attestextract.documents import Document
from attestextract.errors import ConfigurationError
from attestextract.models import ContextOptions, DocumentChunk, ExecutionOptions, ExtractionRequest, GenerationOptions, TaskSpec
from attestextract.pipeline import _request
from attestextract.planning import plan_chunks
from attestextract.providers import resolve_model_config
from attestextract.registry import default_registry
from attestextract.storage import _reject_constant, _unique_object


TLS_MODES = ("auto", "1.2", "auto_fallback")
PATHS = {
    "qwen-chat-object": ("qwen:qwen3.8-27b", "json_object"),
    "deepseek-chat-object": ("deepseek:deepseek-flash", "json_object"),
    "deepseek-responses-object": ("deepseek-responses:deepseek-flash", "json_object"),
    "deepseek-responses-schema": ("deepseek-responses:deepseek-flash", "json_schema"),
}
D1_NAMES = ("attribute", "observation", "relation")
ARTIFACTS = ("sources.json", "tasks.json", "chunks.json", "cases.json", "requests.json",
             "configs.json", "gold.json", "rows.json", "wire-hashes.json", "paper-checklist.json")


def digest(value) -> str:
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _read(path: Path):
    return json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _write(path: Path, value) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def _safe_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).name != name or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ConfigurationError("Experiment artifact is missing or outside the frozen bundle.")
    return path


def runtime_identity() -> dict:
    """Bind installed engine and experiment code without storing local paths."""
    package = Path(attestextract.__file__).resolve().parent
    package_files = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(package.rglob("*.py"))}
    scripts = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(Path(__file__).resolve().parent.glob("provider_experiment*.py"))}
    return {"package_version": attestextract.__version__, "python": platform.python_version(),
            "system": platform.system(), "openssl": ssl.OPENSSL_VERSION, "httpx": httpx.__version__,
            "httpcore": httpcore.__version__, "pydantic": pydantic.__version__,
            "certifi": certifi.__version__,
            "package_files": package_files, "experiment_scripts": scripts}


def network_identity() -> dict:
    """HTTPX uses process proxy/CA settings, not dotenv values as global settings."""
    ca = {}
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        value = os.environ.get(name)
        entry = {"configured": value is not None, "setting_sha256": digest(value)}
        if value:
            path = Path(value)
            if path.is_file():
                entry["content_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                entry["directory_sha256"] = digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                     for p in sorted(path.iterdir()) if p.is_file()})
            else:
                raise ConfigurationError("Configured certificate path does not exist.")
        ca[name] = entry
    return {"proxy_settings_sha256": digest(getproxies()), "certificate_settings": ca,
            "default_ca_sha256": hashlib.sha256(Path(certifi.where()).read_bytes()).hexdigest(),
            "credential_values_bound": False}


def config_key(path_id: str, tls_mode: str) -> str:
    return f"{path_id}--tls-{tls_mode}"


def _git_commit() -> str | None:
    try:
        commit = subprocess.check_output(["git", "-C", str(Path(__file__).resolve().parents[1]),
                                          "rev-parse", "HEAD"], stderr=subprocess.DEVNULL,
                                         text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return commit if len(commit) == 40 and all(ch in "0123456789abcdef" for ch in commit) else None


def _paper_checklist(document: Document, chunks: list[DocumentChunk], supplied: Mapping | None) -> dict:
    if supplied is None:
        supplied = {"status": "not_reviewed", "source_sha256": document.text_sha256,
                    "origin": "Unfilled manual checklist; empty anchors do not establish absence of facts",
                    "items": [{"chunk_id": chunk.chunk_id, "status": "not_reviewed",
                               "body_spans": [s.model_dump() for s in chunk.body_segments or chunk.segments],
                               "headings": [], "conditions": [], "values_and_units": [], "notes": ""}
                              for chunk in chunks]}
    result = json.loads(json.dumps(supplied, ensure_ascii=False, allow_nan=False))
    _validate_checklist(result, document, chunks)
    return result


def _validate_checklist(checklist: dict, document: Document, chunks: list[DocumentChunk]) -> None:
    if (set(checklist) != {"status", "source_sha256", "origin", "items"}
            or checklist["status"] not in {"not_reviewed", "manual_source_checked"}
            or not isinstance(checklist["origin"], str) or not checklist["origin"].strip()
            or checklist["source_sha256"] != document.text_sha256
            or len(checklist["items"]) != len(chunks)):
        raise ConfigurationError("Paper checklist must identify its unchanged source and all planned chunks.")
    for item, chunk in zip(checklist["items"], chunks):
        if (set(item) != {"chunk_id", "status", "body_spans", "headings", "conditions", "values_and_units", "notes"}
                or item["chunk_id"] != chunk.chunk_id
                or item["body_spans"] != [s.model_dump() for s in chunk.body_segments or chunk.segments]
                or item["status"] not in {"not_reviewed", "manual_source_checked"}
                or not isinstance(item["notes"], str)):
            raise ConfigurationError("Paper checklist chunk identity or review state is invalid.")
        if checklist["status"] == "manual_source_checked" and item["status"] != "manual_source_checked":
            raise ConfigurationError("A fully checked checklist cannot contain unreviewed chunks.")
        for name in ("headings", "conditions", "values_and_units"):
            if not isinstance(item[name], list):
                raise ConfigurationError("Checklist anchors must be lists of exact source positions.")
            for anchor in item[name]:
                if (set(anchor) != {"quote", "start", "end"} or not isinstance(anchor["quote"], str)
                        or type(anchor["start"]) is not int or type(anchor["end"]) is not int
                        or not 0 <= anchor["start"] < anchor["end"] <= len(document.text)
                        or document.text[anchor["start"]:anchor["end"]] != anchor["quote"]):
                    raise ConfigurationError("Checklist anchor does not match the exact source text.")


def _configs(environment: Mapping[str, str]) -> dict:
    result = {}
    for path_id, (selector, mode) in PATHS.items():
        for tls_mode in TLS_MODES:
            config = resolve_model_config(selector, environment=environment, overrides={
                "structured_output": mode, "tls_mode": tls_mode,
                "max_attempts": 1, "format_retries": 0,
                "generation": {"temperature": 0, "max_output_tokens": 8192}})
            result[config_key(path_id, tls_mode)] = config.model_dump(mode="json")
    return result


def _rows(chunk_ids: list[str]) -> list[dict]:
    rows = []

    def add(stage, provider, path_id, case_id, round_id, group_id, *, fixed=None, alternatives=None):
        chunk_id = case_id.removeprefix("paper-") if case_id.startswith("paper-") else "chunk-0001"
        row = {"id": f"{stage}-{len(rows) + 1:03d}", "sequence": len(rows) + 1,
               "stage": stage, "provider": provider, "path_id": path_id, "case_id": case_id,
               "round": round_id, "group_id": group_id, "request_id": case_id, "chunk_id": chunk_id}
        if fixed is not None:
            row["config_key"] = fixed
        else:
            row["config_alternatives"] = alternatives
        rows.append(row)

    for chunk_id in chunk_ids:
        add("A", "qwen", "qwen-chat-object", f"paper-{chunk_id}", 1, "A-qwen",
            fixed=config_key("qwen-chat-object", "1.2"))
    # Rotate TLS positions ABC/BCA/CAB. Provider groups alternate; each group's
    # short/long pair shares one backend, without reusing any model response.
    for round_id in range(1, 4):
        offset = round_id - 1
        for mode in TLS_MODES[offset:] + TLS_MODES[:offset]:
            for provider in (("deepseek", "qwen") if round_id == 2 else ("qwen", "deepseek")):
                path_id = f"{provider}-chat-object"
                group = f"B-r{round_id}-{provider}-{mode}"
                for case_id in ("d1-observation", "paper-chunk-0012"):
                    add("B", provider, path_id, case_id, round_id, group, fixed=config_key(path_id, mode))
    for round_id in (1, 2):
        paths = list(PATHS) if round_id == 1 else list(reversed(PATHS))
        for path_id in paths:
            for name in D1_NAMES:
                add("C", path_id.split("-")[0], path_id, f"d1-{name}", round_id,
                    f"C-r{round_id}-{path_id}", alternatives=[config_key(path_id, mode) for mode in TLS_MODES])
    for provider in ("qwen", "deepseek"):
        choices = [config_key(path_id, mode) for path_id in PATHS if path_id.startswith(provider + "-")
                   for mode in TLS_MODES]
        for chunk_id in chunk_ids:
            add("D", provider, f"{provider}-recommended", f"paper-{chunk_id}", 1, f"D-{provider}",
                alternatives=choices)
    return rows


def _wire_hashes(rows, requests, configs) -> dict:
    registry = default_registry()
    backends = {key: registry.create(config["adapter"], config=BackendConfig.model_validate(config))
                for key, config in configs.items()}
    result = {}
    for row in rows:
        request_id = row["request_id"]
        request = ExtractionRequest.model_validate(requests[request_id])
        for key in row.get("config_alternatives", [row.get("config_key")]):
            result.setdefault(request_id, {})[key] = digest(backends[key].prepare_payload(request))
    return result


def prepare_bundle(output: Path, source: Path, task: Path, environment: Mapping[str, str], expected_chunks: int = 16,
                   paper_checklist: Mapping | None = None) -> dict:
    """Prepare fixed A/B/C/D material offline; no API key is needed or saved."""
    if expected_chunks != 16:
        raise ConfigurationError("This approved experiment requires exactly 16 planned paper chunks.")
    source, task, output = Path(source).resolve(), Path(task).resolve(), Path(output).resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ConfigurationError("Private experiment bundles must be saved outside the public repository.")
    if output.exists() or source.is_relative_to(output) or task.is_relative_to(output):
        raise ConfigurationError("Use a new experiment directory separate from the original input files.")
    source_bytes, task_bytes = source.read_bytes(), task.read_bytes()
    text = source_bytes.decode("utf-8")
    task_spec = TaskSpec.model_validate(json.loads(task_bytes.decode("utf-8"), object_pairs_hook=_unique_object,
                                                 parse_constant=_reject_constant))
    generation = GenerationOptions(temperature=0, max_output_tokens=8192)
    options = ExecutionOptions(max_model_calls=16, max_split_depth=0)
    context = ContextOptions()
    chunks = plan_chunks(text, task_spec, generation, options, context=context)
    if len(chunks) != expected_chunks:
        raise ConfigurationError("Paper planning does not match the approved 16-chunk budget; prepare a revised plan before any call.")
    document = Document.from_text(text, source_name=source.name,
                                 representation="text/markdown" if source.suffix.lower() == ".md" else "text/plain")
    sources = {"paper": document.model_dump(mode="json")}
    tasks = {"paper": task_spec.model_dump(mode="json")}
    cases = {"paper": {"source_id": "paper", "task_id": "paper", "gold_id": None}}
    frozen_chunks, requests, gold = {}, {}, {}
    for chunk in chunks:
        case_id = f"paper-{chunk.chunk_id}"
        frozen_chunks[case_id] = chunk.model_dump(mode="json")
        requests[case_id] = _request(task_spec, chunk, generation).model_dump(mode="json")
        cases[case_id] = {"source_id": "paper", "task_id": "paper", "chunk_id": case_id, "gold_id": None}
    for name in D1_NAMES:
        case_id = f"d1-{name}"
        resource = files("attestextract").joinpath("examples", "d1", name)
        short_text = resource.joinpath("source.txt").read_bytes().decode("utf-8")
        short_task = TaskSpec.model_validate_json(resource.joinpath("task.json").read_text(encoding="utf-8"))
        short_generation = GenerationOptions(temperature=0, max_output_tokens=2048)
        short_chunks = plan_chunks(short_text, short_task, short_generation, options, context=context)
        if len(short_chunks) != 1:
            raise ConfigurationError("Each D1 acceptance sample must remain a single chunk.")
        sources[case_id] = Document.from_text(short_text, source_name=f"{case_id}.txt").model_dump(mode="json")
        tasks[case_id] = short_task.model_dump(mode="json")
        frozen_chunks[case_id] = short_chunks[0].model_dump(mode="json")
        requests[case_id] = _request(short_task, short_chunks[0], short_generation).model_dump(mode="json")
        # The attribute archive contains a deliberate rejected candidate. Only
        # expected.records is extraction gold, never candidate_count/reviews.
        expected = json.loads(resource.joinpath("expected.json").read_text(encoding="utf-8"))
        gold[case_id] = expected["records"]
        cases[case_id] = {"source_id": case_id, "task_id": case_id, "chunk_id": case_id, "gold_id": case_id}
    configs = _configs(environment)
    rows = _rows([chunk.chunk_id for chunk in chunks])
    artifacts = dict(zip(ARTIFACTS, (sources, tasks, frozen_chunks, cases, requests, configs, gold, rows,
                                     _wire_hashes(rows, requests, configs), _paper_checklist(document, chunks, paper_checklist))))
    extraction = {"source_name": source.name, "representation": document.representation,
                  "max_chunk_chars": 6000, "table_rows_per_chunk": 12, "context": context.model_dump(),
                  "options": options.model_dump(), "generation": generation.model_dump()}
    manifest = {"bundle_version": "0.1", "package_version": attestextract.__version__, "private": True,
                "output_protocol": "flat-v1", "result_schema_version": "0.4", "network_calls": 0,
                "expected_paper_chunks": 16, "stage_limits": {"A": 16, "B": 36, "C": 24, "D": 32},
                "max_model_calls": 108, "max_output_tokens_total": sum(
                    requests[row["request_id"]]["generation"]["max_output_tokens"] for row in rows),
                "format_retries": 0, "max_attempts": 1, "max_split_depth": 0,
                "extraction": extraction, "runtime": runtime_identity(), "network_identity": network_identity(),
                "gold_origin": "Frozen synthetic D1 manual expectations; not expert-reviewed real-paper gold",
                "provenance": {"git_commit": _git_commit()},
                "identity_notice": "Hashes detect changes, not authenticity. Credential values are neither stored nor bound.",
                "input_sha256": {"source_bytes": hashlib.sha256(source_bytes).hexdigest(),
                                 "task_bytes": hashlib.sha256(task_bytes).hexdigest()}}
    output.mkdir(parents=True, exist_ok=False)
    for name, value in artifacts.items():
        _write(output / name, value)
    manifest["files"] = {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in ARTIFACTS}
    manifest["bundle_id"] = "provider-experiment-" + digest(manifest)
    _write(output / "manifest.json", manifest)
    return manifest


def check_environment(bundle: dict, environment: Mapping[str, str]) -> None:
    """Revalidate endpoint/key-variable bindings without binding credential values."""
    if _configs(environment) != bundle["configs"] or network_identity() != bundle["manifest"]["network_identity"]:
        raise ConfigurationError("Provider bindings or network environment changed; create a new frozen experiment.")


def load_bundle(root: Path, environment: Mapping[str, str] | None = None) -> dict:
    """Check all frozen inputs, runtime and request shapes offline, then resolve references."""
    root = Path(root).resolve()
    try:
        manifest = _read(_safe_file(root, "manifest.json"))
        identity = {key: value for key, value in manifest.items() if key != "bundle_id"}
        if (manifest.get("bundle_version") != "0.1" or manifest.get("bundle_id") != "provider-experiment-" + digest(identity)
                or set(manifest["files"]) != set(ARTIFACTS)):
            raise ValueError("manifest")
        if manifest["runtime"] != runtime_identity() or manifest["network_identity"] != network_identity():
            raise ConfigurationError("Engine, experiment code or network environment changed; create a new frozen experiment.")
        data = {}
        for name in ARTIFACTS:
            path = _safe_file(root, name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"][name]:
                raise ValueError("artifact checksum")
            data[name.removesuffix(".json").replace("-", "_")] = _read(path)
        _validate_materials(manifest, data)
        result = {"manifest": manifest, **data, "extraction": manifest["extraction"]}
        if environment is not None:
            check_environment(result, environment)
        result["cases"] = {key: {**case, "source": data["sources"][case["source_id"]]["text"],
                                  "task": data["tasks"][case["task_id"]],
                                  "gold": data["gold"].get(case["gold_id"]),
                                  "chunk": data["chunks"].get(case.get("chunk_id"))}
                           for key, case in data["cases"].items()}
        result["rows"] = [{**row, "request": data["requests"][row["request_id"]]} for row in data["rows"]]
        return result
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise ConfigurationError("Frozen experiment is missing, damaged or inconsistent; no request was sent.") from None


def _validate_materials(manifest: dict, data: dict) -> None:
    sources, tasks = data["sources"], data["tasks"]
    for source in sources.values():
        Document.model_validate(source)
    for task in tasks.values():
        TaskSpec.model_validate(task)
    extraction = manifest["extraction"]
    paper = sources["paper"]
    if (extraction["source_name"] != paper["source_name"] or extraction["representation"] != paper["representation"]
            or extraction["generation"] != GenerationOptions(temperature=0, max_output_tokens=8192).model_dump()
            or extraction["options"] != ExecutionOptions(max_model_calls=16, max_split_depth=0).model_dump()
            or extraction["context"] != ContextOptions().model_dump()
            or extraction["max_chunk_chars"] != 6000 or extraction["table_rows_per_chunk"] != 12
            or manifest["output_protocol"] != "flat-v1" or manifest["result_schema_version"] != "0.4"
            or manifest["input_sha256"]["source_bytes"] != paper["text_sha256"]):
        raise ValueError("paper extraction settings")
    chunks = plan_chunks(paper["text"], TaskSpec.model_validate(tasks["paper"]),
                         GenerationOptions.model_validate(extraction["generation"]),
                         ExecutionOptions.model_validate(extraction["options"]),
                         max_chars=extraction["max_chunk_chars"], table_rows_per_chunk=extraction["table_rows_per_chunk"],
                         context=ContextOptions.model_validate(extraction["context"]))
    if len(chunks) != 16 or data["rows"] != _rows([chunk.chunk_id for chunk in chunks]):
        raise ValueError("approved plan")
    _validate_checklist(data["paper_checklist"], Document.model_validate(paper), chunks)
    if (manifest["stage_limits"] != {"A": 16, "B": 36, "C": 24, "D": 32}
            or manifest["max_model_calls"] != 108 or manifest["format_retries"] != 0
            or manifest["max_attempts"] != 1 or manifest["max_split_depth"] != 0):
        raise ValueError("approved limits")
    for chunk in chunks:
        if data["chunks"][f"paper-{chunk.chunk_id}"] != chunk.model_dump(mode="json"):
            raise ValueError("paper chunks")
    for case_id, case in data["cases"].items():
        if case_id == "paper":
            continue
        source = sources[case["source_id"]]["text"]
        chunk = DocumentChunk.model_validate(data["chunks"][case["chunk_id"]])
        if any(span.end > len(source) for span in chunk.segments) or "".join(source[s.start:s.end] for s in chunk.segments) != chunk.text:
            raise ValueError("source chunk references")
        generation = GenerationOptions(temperature=0, max_output_tokens=8192 if case_id.startswith("paper-") else 2048)
        expected = _request(TaskSpec.model_validate(tasks[case["task_id"]]), chunk, generation).model_dump(mode="json")
        if data["requests"][case_id] != expected:
            raise ValueError("frozen unified request")
        if case["gold_id"] is not None:
            for record in data["gold"][case["gold_id"]]:
                for field in record["fields"].values():
                    if source[field["start"]:field["end"]] != field["quote"]:
                        raise ValueError("gold source position")
    for config in data["configs"].values():
        checked = BackendConfig.model_validate(config)
        if checked.max_attempts != 1 or checked.format_retries != 0 or checked.timeout is not None or checked.read_timeout is not None:
            raise ValueError("backend limits")
    if data["wire_hashes"] != _wire_hashes(data["rows"], data["requests"], data["configs"]):
        raise ValueError("frozen wire request")
    if manifest["max_output_tokens_total"] != sum(data["requests"][row["request_id"]]["generation"]["max_output_tokens"]
                                                   for row in data["rows"]):
        raise ValueError("output budget")
