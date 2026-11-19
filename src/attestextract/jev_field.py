"""Build an independent native Jev choice question for the proposed cell value."""

import json


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
