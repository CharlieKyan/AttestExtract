"""Review frozen observations with a local decision model and export a separate dataset."""

from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .dataset import _confirmed_corroborations
from .dataset_io import _read_json_object, save_dataset
from .execution import digest
from .jev_backend import validate_choice
from .jev_field import field_request
from .jev_packets import make_packets, needs_review
from .jev_projection import final_dataset


_APP_ID = 0x41584A56
_PROTOCOL = "jev-dataset-v1"


def _initialize(db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        app = db.execute("PRAGMA application_id").fetchone()[0]
        if app == 0 and not db.execute("SELECT 1 FROM sqlite_master").fetchone():
            db.execute(f"PRAGMA application_id={_APP_ID}")
            db.execute("CREATE TABLE answers (job TEXT PRIMARY KEY, response TEXT NOT NULL, checksum TEXT NOT NULL)")
        elif app != _APP_ID:
            raise ValueError("Use a dedicated Jev review ledger.")


def _predict(db, job, engine, request):
    # Local inference has no remote side effect. A crash rolls back this one
    # decision. Concurrent runs using the same ledger reuse the committed answer.
    criteria = request["questions"]["disposition"]["criteria"]
    with db:
        db.execute("BEGIN IMMEDIATE")
        entry = db.execute("SELECT response, checksum FROM answers WHERE job=?", (job,)).fetchone()
        if entry is None:
            answer = engine.predict(request)
        else:
            answer = json.loads(entry[0])
            if digest([job, answer]) != entry[1]:
                raise ValueError("Invalid Jev ledger receipt checksum.")
        choice, confidence = validate_choice(answer, criteria)
        if entry is None:
            db.execute("INSERT INTO answers VALUES (?,?,?)",
                       (job, json.dumps(answer, ensure_ascii=False, allow_nan=False), digest([job, answer])))
    return {"choice": choice, "confidence": confidence, "response": answer, "job_id": job}, entry is not None


def review_dataset(source_path, dataset_path, output_dir, engine, *, ledger_path=None, progress=None):
    """Run the complete pending observation queue. Reuse scored packets on restart."""
    source_path, dataset_path = Path(source_path).resolve(), Path(dataset_path).resolve()
    if dataset_path.is_dir():
        dataset_path = dataset_path / "dataset.json"
    target = Path(output_dir).resolve()
    ledger = Path(ledger_path).resolve() if ledger_path else target.with_name(target.name + ".sqlite")
    if target.exists() or target == ledger:
        raise ValueError(f"Choose a new Jev dataset output directory: {target}")
    if (ledger.suffix.lower() != ".sqlite" or ledger in (source_path, dataset_path)
            or target in ledger.parents
            or dataset_path.name == "dataset.json" and any(
                dataset_path.parent in p.parents for p in (ledger, target))):
        raise ValueError("Keep the new output and its .sqlite ledger outside the frozen dataset directory.")
    data = _read_json_object(dataset_path)
    source = source_path.read_bytes().decode("utf-8")
    if data.get("dataset_version") != "2" or data.get("source_sha256") != sha256(source.encode()).hexdigest():
        raise ValueError("Expected a version 2 dataset and its byte-identical UTF-8 source.")
    if "jev_review" in data:
        raise ValueError("Use the original dataset for Jev review or resume.")
    packets = list(make_packets(data, source))
    published = _confirmed_corroborations(data["observations"])
    expected = [o["observation_id"] for o in data["observations"] if needs_review(data, o, published)]
    ids = [p["observation_id"] for p in packets]
    if len(set(ids)) != len(ids) or sorted(ids) != sorted(expected):
        raise ValueError("Jev queue does not account for every pending observation exactly once.")
    decisions, failures, first_pass, field_checks, reused = {}, {}, {}, {}, 0
    ledger.parent.mkdir(parents=True, exist_ok=True)
    model_identity = engine.identity if packets else None
    with closing(sqlite3.connect(ledger, timeout=180)) as db:
        _initialize(db)
        for i, packet in enumerate(packets, 1):
            oid = packet["observation_id"]
            # Option order affects the tokens used by a choice model.
            encoded = json.dumps(packet["request"], ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            job = digest([_PROTOCOL, model_identity, data["source_sha256"], oid, encoded, packet["options"]])
            try:
                decision, cached = _predict(db, job, engine, packet["request"])
                if packet.get("review_axis") == "field_type":
                    field_checks[oid] = {"request": packet["request"], "decision": decision}
                else:
                    first_pass[oid] = decision
                if decision["choice"] in packet["options"] and packet["options"][decision["choice"]].get("role") != "filing_subject":
                    # Judge the literal value against the field independently of the selected owner.
                    question = field_request(packet)
                    field_job = digest([job, "field-check", json.dumps(question, ensure_ascii=False)])
                    checked, from_cache = _predict(db, field_job, engine, question)
                    field_checks[oid] = {"request": question, "decision": checked}
                    cached = cached and from_cache
                    if checked["choice"] != "supported":
                        decision = {**checked, "choice": "review", "basis": "field_check",
                                    "field_verdict": checked["choice"]}
            except ValueError as exc:
                failures[oid] = str(exc)
                state = "error"
            else:
                decisions[oid] = decision
                reused += int(cached)
                state = "cached" if cached else decision["choice"]
            if progress:
                progress(i, len(packets), state)
    final = final_dataset(data, packets, decisions)
    for obs in final["observations"]:
        check = field_checks.get(obs["observation_id"])
        if check:
            obs["jev_review"]["field_verdict"] = check["decision"]["choice"]
            obs["jev_review"]["field_job_id"] = check["decision"]["job_id"]
    counts = Counter(obs["delivery"] for obs in final["observations"])
    outcomes = Counter(obs["jev_review"]["state"] for obs in final["observations"] if "jev_review" in obs)
    records = []
    for packet in packets:
        oid = packet["observation_id"]
        records.append({**packet, "subject_check": first_pass.get(oid), "field_check": field_checks.get(oid),
                        "decision": decisions.get(oid), "error": failures.get(oid)})
    final["jev_review"] = {"protocol": _PROTOCOL, "model": "Intern-Decision-4B", "model_identity": model_identity,
                           "baseline_digest": digest(data), "queued": len(packets), "scored": len(decisions),
                           "cached": reused, "errors": len(failures), "observations_by_delivery": dict(counts),
                           "outcomes": dict(outcomes), "baseline_rows": len(data["rows"]), "final_rows": len(final["rows"]),
                           "subject_checks": len(first_pass), "field_checks": len(field_checks),
                           "items": records}
    destination = target.with_name(target.name + ".partial-" + uuid4().hex[:8]) if failures else target
    save_dataset(final, destination)
    return destination, final["jev_review"]
