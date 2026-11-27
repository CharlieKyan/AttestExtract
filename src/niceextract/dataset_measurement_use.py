"""Ground the use of quantitative table cells in their own caption and local layout."""

from collections import defaultdict
from decimal import Decimal
import re

from .dataset_statistics import value_statistic


_CAPTION = re.compile(r"^\s*(?:table|表)\s*([0-9]+)\b", re.I)
_CLASSIFICATION = re.compile(r"\bclassification\b|分类", re.I)
_SENSITIVITY = re.compile(r"\bsensitivity analys(?:is|es)\b|敏感性分析", re.I)
_INPUT_RANGE = re.compile(r"\b(?:range[s]? of (?:input )?parameters?|(?:input|parameter) ranges?)\b|参数(?:的)?取值范围", re.I)
_MEASURED = re.compile(
    r"^\s*(?:(?:table|表)\s*[0-9]+[\s:：.．–—-]*)?"
    r"(?:measured\s+|observed\s+|measurements(?:\s+of\b|\s*$)|实测|观测值)", re.I)
_MIXED = re.compile(r"\b(?:not|versus|vs\.?|predicted and|and predicted|and simulated|simulated and)\b|预测与|与预测|模拟与|与模拟|非实测", re.I)
_OTHER_USE = re.compile(
    r"\b(?:predicted|estimated|target|expected|simulated|modeled|modelled|setpoint|thresholds?|"
    r"rated|ratings?|nominal|specifications?)\b|预测|预计|模型设定|理论值|阈值|额定|标称|规格", re.I)


def _captions(source, table):
    if table.captions:
        return [{"start": s.start, "end": s.end, "quote": source[s.start:s.end]} for s in table.captions]
    # Plain OCR Markdown frequently has a numbered caption in two languages.
    # Only accept immediately preceding caption lines for the same printed number.
    end = table.span.start
    captions = []
    number = None
    while end and source[end - 1].isspace():
        end -= 1
    while end:
        start = source.rfind("\n", 0, end) + 1
        line = source[start:end]
        title = _CAPTION.match(line)
        if not title or (number is not None and title[1] != number):
            break
        number = title[1]
        captions.append({"start": start, "end": end, "quote": line})
        end = start
        while end and source[end - 1].isspace():
            end -= 1
    return captions


def _class_boundaries(table, numeric):
    # A broad classification caption can cover both defining cutoffs and
    # measurements grouped by class. Elect only a series that partitions an
    # axis using two open tails and touching inner intervals.
    axes = defaultdict(list)
    for cell in table.cells:
        if cell.cell_id in numeric and cell.rowspan == cell.colspan == 1:
            axes["row", cell.row].append((cell.column, cell.cell_id))
            axes["column", cell.column].append((cell.row, cell.cell_id))
    result = {}
    for axis in axes.values():
        if len(axis) < 2:
            continue
        axis.sort()
        if axis[-1][0] - axis[0][0] + 1 != len(axis):
            continue
        ids = [cell for _, cell in axis]
        if "lower_bound" in numeric[ids[0]]:
            ids.reverse()
        if "upper_bound" not in numeric[ids[0]] or "lower_bound" not in numeric[ids[-1]]:
            continue
        if any("min" not in numeric[key] or "max" not in numeric[key] for key in ids[1:-1]):
            continue
        if all(_adjacent(numeric[a], numeric[b]) for a, b in zip(ids, ids[1:])):
            for key in ids:
                result[key] = ids
    return result


def _adjacent(left, right):
    return Decimal(left.get("upper_bound", left.get("max"))) == Decimal(right.get("lower_bound", right.get("min")))


def table_measurement_uses(source, structure):
    assignments = {}
    for table in structure.tables:
        captions = _captions(source, table)
        title = " ".join(c["quote"] for c in captions)
        if not title or _MIXED.search(title):
            continue
        classification = _CLASSIFICATION.search(title)
        sensitivity = _SENSITIVITY.search(title) and _INPUT_RANGE.search(title)
        measured = any(_MEASURED.match(c["quote"]) for c in captions)
        if sum(bool(x) for x in (classification, sensitivity, measured)) != 1:
            continue
        quantities = {}
        citations = {}
        mixed_measurements = measured and _OTHER_USE.search(title)
        for cell in table.cells:
            raw = source[cell.span.start:cell.span.end]
            if measured and _OTHER_USE.search(raw):
                mixed_measurements = True
            _, parts = value_statistic(source, {"value": raw}, None, [])
            if parts:
                quantities[cell.cell_id] = parts
                citations[cell.cell_id] = {"start": cell.span.start, "end": cell.span.end,
                                           "quote": raw, "ref": cell.cell_id}
        if classification:
            for key, partition in _class_boundaries(table, quantities).items():
                tails = [citations[partition[0]], citations[partition[-1]]]
                assignments[key] = "classification_threshold", captions + tails
        elif sensitivity:
            for key, parts in quantities.items():
                if "min" in parts:
                    assignments[key] = "sensitivity_input_range", captions
        elif not mixed_measurements:
            for key in quantities:
                assignments[key] = "measured", captions
    return assignments
