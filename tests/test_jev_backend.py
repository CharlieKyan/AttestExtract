import copy
import json
import shutil

import pytest

from attestextract.errors import ConfigurationError
from attestextract.jev_backend import LocalJev, MODEL_NAME, validate_choice


OPTIONS = {"supported": "The proposed assignment is supported.", "unknown": "Evidence is insufficient."}
QUESTION = {"type": "choice", "instructions": "Assign the reading.", "criteria": OPTIONS}
ANSWER = {"model": MODEL_NAME, "answers": {"disposition": {
    "type": "choice", "choice": "unknown", "decision": "unknown", "confidence": 0.8,
    "probabilities": {"supported": 0.2, "unknown": 0.8}}}, "usage": {"input_tokens": 127}}


def _checkpoint(path):
    path.mkdir()
    (path / "inference.py").write_text(
        "from dataclasses import dataclass\n"
        f"MODEL_NAME = {MODEL_NAME!r}\n"
        "@dataclass\nclass Result:\n    count: int\n"
        "class DecisionEngine:\n"
        "    def __init__(self, checkpoint, device, dtype):\n"
        "        assert device == 'cpu' and dtype == 'float32'\n"
        "        self.count = 0\n"
        "    def predict(self, request):\n"
        "        assert request['state'] == {'quote': 'Reading 12'}\n"
        "        self.count += 1\n"
        f"        return dict({ANSWER!r}, count=self.count)\n",
        encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"synthetic weights")
    return path


def test_native_predict_is_lazy_and_reuses_one_text_scorer(tmp_path):
    model = LocalJev(_checkpoint(tmp_path / "model"), device="cpu")
    assert "_engine" not in vars(model)
    request = {"state": {"quote": "Reading 12"}, "questions": {"disposition": QUESTION}}
    saved = copy.deepcopy(request)
    for n in (1, 2):
        response = model.predict(request)
        assert response == {**ANSWER, "count": n}
        assert validate_choice(response, OPTIONS) == ("unknown", 0.8)
    assert request == saved


def test_checkpoint_identity_tracks_weights_code_and_tokenization_across_paths(tmp_path):
    model = _checkpoint(tmp_path / "first")
    replica = tmp_path / "second"
    shutil.copytree(model, replica)
    first = LocalJev(model).identity
    assert LocalJev(replica).identity == first
    for name in ("model.safetensors", "inference.py", "tokenizer.json"):
        path = replica / name
        original = path.read_bytes() if path.exists() else None
        path.write_bytes(b"same-size-change" if original is None else original[:-1] + b"x")
        assert LocalJev(replica).identity != first
        if original is None:
            path.unlink()
        else:
            path.write_bytes(original)
    assert LocalJev(replica, "cpu").identity != first


def test_incomplete_weights_cannot_share_a_resume_identity(tmp_path):
    model = _checkpoint(tmp_path / "model")
    (model / "model.safetensors").unlink()
    with pytest.raises(ConfigurationError, match="weights"):
        _ = LocalJev(model).identity
    (model / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"param": "shard.safetensors"}}))
    with pytest.raises(ConfigurationError, match="shard.safetensors"):
        _ = LocalJev(model).identity


def test_request_must_be_one_local_text_choice(tmp_path):
    backend = LocalJev(_checkpoint(tmp_path / "model"))
    request = {"state": {}, "questions": {"disposition": QUESTION}}
    for invalid in ({**request, "images": ["figure.jpg"]}, {**request, "questions": {}},
                    {**request, "questions": {"disposition": {"type": "score"}}}):
        with pytest.raises(ValueError):
            backend.predict(invalid)
    assert "_engine" not in vars(backend)


@pytest.mark.parametrize("alter", [
    {"choice": "supported"}, {"decision": "supported"}, {"confidence": 0.9}, {"confidence": True},
    {"probabilities": {"supported": 0, "unknown": True}}, {"type": "score"},
    {"probabilities": {"unknown": 0.8, "new": 0.2}},
    {"probabilities": {"unknown": float("nan"), "supported": 0.2}},
    {"probabilities": {"unknown": 1.2, "supported": -0.2}},
    {"probabilities": {"unknown": 0.8, "supported": 0.1}},
])
def test_unusable_native_answers_are_rejected(alter):
    response = copy.deepcopy(ANSWER)
    response["answers"]["disposition"].update(alter)
    with pytest.raises(ValueError):
        validate_choice(response, OPTIONS)


def test_result_model_question_set_and_ties_are_validated():
    for invalid in ({**ANSWER, "model": "different"}, {**ANSWER, "answers": {}},
                    {**ANSWER, "answers": {**ANSWER["answers"], "added": None}}):
        with pytest.raises(ValueError):
            validate_choice(invalid, OPTIONS)
    tied = copy.deepcopy(ANSWER)
    tied["answers"]["disposition"].update(choice="supported", decision="supported", confidence=0.5,
                                           probabilities=dict.fromkeys(OPTIONS, 0.5))
    assert validate_choice(tied, OPTIONS) == ("supported", 0.5)
