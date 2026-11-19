"""Optional local adapter for the native Intern-Decision-4B choice scorer."""

from __future__ import annotations

from functools import cached_property
import hashlib
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

from .errors import ConfigurationError
from .execution import digest


MODEL_NAME = "Intern-Decision-4B"


class LocalJev:
    """Use inference.py from the explicitly supplied, trusted local checkpoint."""

    def __init__(self, checkpoint: str | Path, device: str = "cuda"):
        self.checkpoint = Path(checkpoint).resolve()
        if not (self.checkpoint / "inference.py").is_file():
            raise ConfigurationError("Select a local Intern-Decision-4B checkpoint containing inference.py.")
        if not re.fullmatch(r"cpu|cuda(?::[0-9]+)?", device):
            raise ConfigurationError("Jev device must be cpu, cuda or cuda:<index>.")
        self.device = device

    @cached_property
    def identity(self) -> str:
        """Fingerprint weights, executable, tokenizer and runtime once per run."""
        root = self.checkpoint
        files = {p for p in root.iterdir() if p.is_file() and (
            p.suffix in {".json", ".py", ".safetensors", ".jinja", ".model"}
            or p.name in {"merges.txt", "vocab.txt"})}
        files.update((root / "chat_templates").glob("*.jinja"))
        index = root / "model.safetensors.index.json"
        if index in files:
            try:
                shards = json.loads(index.read_bytes())["weight_map"]
                if not isinstance(shards, dict) or not shards:
                    raise ValueError
                for name in set(shards.values()):
                    file = root / name
                    if not file.is_relative_to(root) or ".." in file.parts or file.suffix != ".safetensors":
                        raise ValueError
                    files.add(file)
            except (KeyError, OSError, TypeError, ValueError) as exc:
                raise ConfigurationError("Invalid local safetensors weight index.") from exc
        elif root / "model.safetensors" not in files:
            raise ConfigurationError("Local Jev weights are missing (model.safetensors or its shard index).")
        hashes = {}
        for path in sorted(files):
            try:
                with path.open("rb") as stream:
                    hashes[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError as exc:
                raise ConfigurationError(f"Cannot read local Jev checkpoint file: {path.name}") from exc
        runtime = {}
        for name in ("torch", "torchvision", "transformers", "tokenizers", "Pillow"):
            try:
                runtime[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                runtime[name] = None
        return MODEL_NAME + ":" + digest({"files": hashes, "runtime": runtime, "device": self.device})

    @cached_property
    def _engine(self):
        path = self.checkpoint / "inference.py"
        name = "_attestextract_jev_" + self.identity.split(":")[-1]
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # Native dataclasses look up their module during execution.
        try:
            # Load the source itself, including when a checkpoint was replaced in place.
            exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
        except (ImportError, SyntaxError) as exc:
            raise ConfigurationError(
                f"Cannot load local Jev inference.py ({exc}). Use the checkpoint's Python / PyTorch / Transformers environment."
            ) from exc
        finally:
            sys.modules.pop(name, None)
        if getattr(module, "MODEL_NAME", None) != MODEL_NAME or not hasattr(module, "DecisionEngine"):
            raise ConfigurationError("The selected inference.py must provide the native Intern-Decision-4B DecisionEngine.")
        return module.DecisionEngine(checkpoint=str(self.checkpoint), device=self.device,
                                     dtype="float32" if self.device == "cpu" else "bfloat16")

    def predict(self, request: dict) -> dict:
        """Score one text-only disposition request and retain its full native response."""
        if not isinstance(request, dict) or set(request) != {"state", "questions"}:
            raise ValueError("Supply state and one text-only disposition question.")
        questions = request["questions"]
        if not isinstance(questions, dict) or set(questions) != {"disposition"}:
            raise ValueError("Supply exactly one question named disposition.")
        question = questions["disposition"]
        if not isinstance(question, dict) or question.get("type") != "choice":
            raise ValueError("The disposition question must use type choice.")
        criteria = question.get("criteria")
        _check_criteria(criteria)
        response = self._engine.predict(request)
        validate_choice(response, criteria)
        return response


def _check_criteria(criteria):
    if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 62 or not all(
        isinstance(key, str) and key.strip() and isinstance(description, str) and description.strip()
        for key, description in criteria.items()
    ):
        raise ValueError("Supply 2–62 named disposition options with nonempty descriptions.")


def _probability(value):
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def validate_choice(response: dict, criteria: dict) -> tuple[str, float]:
    """Check the entire submitted choice distribution, including its declared winner."""
    _check_criteria(criteria)
    if not isinstance(response, dict) or response.get("model") != MODEL_NAME:
        raise ValueError("Unexpected Jev response model.")
    answers = response.get("answers")
    if not isinstance(answers, dict) or set(answers) != {"disposition"}:
        raise ValueError("Expected exactly one disposition answer.")
    answer = answers["disposition"]
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("Malformed disposition answer.")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or probabilities.keys() != criteria.keys() or not all(
        _probability(p) for p in probabilities.values()
    ) or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-4):
        raise ValueError("Invalid disposition probability distribution.")
    best = min(probabilities, key=lambda key: (-probabilities[key], key))
    confidence = answer.get("confidence")
    if (answer.get("choice") != best or answer.get("decision", best) != best
            or not _probability(confidence) or not math.isclose(confidence, probabilities[best], abs_tol=1e-7)):
        raise ValueError("Jev choice or confidence disagrees with the probability distribution.")
    return best, float(confidence)
