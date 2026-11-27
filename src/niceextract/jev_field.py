"""Build an independent native Jev choice question for the proposed cell value."""

import json


def type_request(dataset, obs, proof):
    rules = dataset.get("field_types", {}).get(obs["field"]) or dataset.get("field_policies", {}).get(obs["field"])
    state = {"字段": obs["field"], "用户定义": dataset["field_definitions"][obs["field"]], "类型及其来源": rules,
             "原值": obs["value"], "原单位": obs.get("unit"), "条件": obs.get("conditions"),
             "直接对象": obs.get("entity"), "模型所述属性": obs.get("source_property"), "模型对原值的解释": obs.get("value_role"),
             "待核原因": obs["field_type_reason"], "原文与表头": list(dict.fromkeys(s["quote"] for s in proof))}
    encoded = json.dumps(state, ensure_ascii=False)
    criteria = {"supported": "原值可以按字段定义使用，包括以数字书写的名称或编号。",
                "mismatch": "原值表示其他属性，或作者报告的形式不符合用户明确限定的用途。",
                "uncertain": "已有原文不足以区分。"}
    guards = []
    if not proof or len(encoded.encode("utf8")) > 6400:
        guards = ["incomplete_field_evidence"]
        encoded = "本条无法在核验窗口中提供完整的字段定义与原文。"
        criteria = {"uncertain": "保留当前待核记录。", "more_source": "需要原文或完整表头。"}
    instructions = ("直接对象和所在行已经确定。请结合字段定义和原文，判断这个值本身能否作为所问字段使用。"
                    "区分名称或编号与测量值，单值、范围与界限，并留意单位属于哪个量。模型推定的字段类型也可能有误。")
    return {"state": encoded, "questions": {"disposition": {
        "type": "choice", "instructions": instructions, "criteria": criteria}}}, guards


def field_request(packet):
    """Ask whether the actual cell value answers the field, using its original source."""
    proposed = json.loads(packet["request"]["state"])
    observation = proposed["observation"]
    state = {"字段定义": proposed["definition"], "候选值": observation["value"], "单位": observation["unit"],
             "原文引文": [span["quote"] for span in proposed["source"]]}
    question = {
        "type": "choice",
        "instructions": (
            "审核将要写入数据列的候选值。只判断「候选值＋单位」本身能否作为字段定义所要求的答案。"
            "先分辨定义要求的属性，再读候选值实际陈述的属性；原文只用于解释原值。"
            "即使该段原文还提供了其他资料，也不能用未被抽进候选值的细节替它作答。"
            "证据中的局部编号或数量、结构、范围或背景叙述只有在它们本身回答所问属性时才能接受。"
            "不要求逐字使用字段名。选择一个判断。"
        ),
        "criteria": {
            "supported": "原值本身给出了所问属性的实际内容，原文支持。",
            "mismatch": "原值给出另一种属性或只给相关背景，没有给出所问属性的实际内容。",
            "uncertain": "原值与原文仍不足以判定是否符合字段定义。",
        },
    }
    return {"state": state, "questions": {"disposition": question}}
