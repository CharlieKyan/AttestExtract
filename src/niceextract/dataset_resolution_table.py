"""Verify local cell paths against the physical grid of the original source."""

from collections import defaultdict
import re
from .table_notation import numeric_cell


def reconcile_table_unit(item, path):
    """Preserve the original warning when a checked local pair resolves its unit reference."""
    ref = (path or {}).get("reconciled_unit_ref")
    if not ref:
        return item
    resolved = [b for b in item.get("blockers", []) if b.get("code") == "relation_geometry_mismatch"
                and b.get("field") == "unit" and (b.get("source") or {}).get("ref") == ref]
    if not resolved:
        return item
    return {**item, "archived_blockers": item.get("archived_blockers", item["blockers"]),
            "blockers": [b for b in item["blockers"] if b not in resolved], "resolved_table_checks": resolved}


def incompatible_owner_axis(path, row_subject, definitions):
    """Identify an explicitly excluded row axis, preserving the local owner and its source."""
    if not path or not path.get("owner"):
        return None
    owner = path["owner"]["quote"].strip().casefold()
    if path.get("scope") == "group" or owner in {"小计", "合计", "总计", "subtotal", "total", "grand total"}:
        individual = re.search(r"\b(?:individual|individually named)\b|具体|单个|逐个", row_subject, re.I)
        # An explicit exclusion such as “excluding group totals” does not add
        # aggregates to the requested population. Keep a later inclusion intact.
        included = re.sub(r"\b(?:excluding|exclude|without|do not include|not including)\b"
                          r"[^.,;\n]*?(?=\b(?:but|include|including)\b|[.,;\n]|$)"
                          r"|(?:不包含|不包括|排除)[^，。；,;\n]*?(?=但|[，。；,;\n]|$)",
                          "", row_subject, flags=re.I)
        aggregates = re.search(r"\b(?:subtotal|total|aggregate|group)s?\b|小计|合计|总计|组合|汇总", included, re.I)
        if individual and not aggregates:
            return "aggregate_on_individual_axis"
    role = (path.get("owner_role") or {}).get("quote", "").strip().casefold()
    if not role:
        return None
    for definition in definitions:
        for m in re.finditer(r"(?:不把|不将)([^，。；,.;]+?)(?:自动)?(?:当作|作为|视作|视为)", definition):
            terms = re.split(r"、|或者|或|和", m[1])
            if any(role == term.strip() or role + "名称" == term.strip() for term in terms):
                return "task_excludes_owner_axis"
        for m in re.finditer(r"\bdo not (?:treat|use) (.+?) as\b", definition, re.I):
            terms = re.split(r",|\s+(?:and|or)\s+", m[1].casefold())
            if any(term.strip() in {role, role + " names", role + " labels"} for term in terms):
                return "task_excludes_owner_axis"
    return None


def _covers(cell, value, axis, span):
    return (getattr(cell, axis) <= getattr(value, axis)
            and getattr(cell, axis) + getattr(cell, span) >= getattr(value, axis) + getattr(value, span))


def table_type_lists(source, observations):
    """Find an explicit local enumeration of types that recur as distinct table-owner cells."""
    inventories = defaultdict(dict)
    for obs in observations:
        name = obs.get("entity")
        for e in obs["source_evidence"]:
            ref = re.fullmatch(r"(.+)-r[0-9]+-c[0-9]+", e.get("ref") or "")
            if ref and "entity" in e["fields"] and e.get("span") and name and e["quote"].strip() == name:
                inventories[ref[1]][name] = e["span"]["start"]
    output = {}
    for table, positions in inventories.items():
        first = min(positions.values())
        # A floating table can appear after a heading change. Only an explicit
        # reference to its printed number carries a type list across that boundary.
        start = max(0, first - 4000)
        text = source[start:first]
        cut = max((m.end() for m in re.finditer(r"</table>", text, re.I)), default=0)
        text = text[cut:].split("<table", 1)[0]
        heading = max((m.end() for m in re.finditer(r"^#{1,6}[ \t].*$", text, re.M)), default=0)
        caption = re.findall(r"^\s*(?:Table|表)\s*([0-9]+)\b", "\n".join(text.strip().splitlines()[-3:]), re.I | re.M)
        for match in re.finditer(r"(?:[两二三四五六七八九十\d]+种[^，。；()（）\n]{1,24}|"
                                 r"\b(?:types|kinds|classes)\s+of\s+[^.,;()\n]{1,40})\s*[（(]([^()（）\n]{3,100})[)）]",
                                 text, re.I):
            clause = re.split(r"[.。;；\n]", text[:match.start()])[-1]
            if re.search(r"\b(?:not|unlike|except)\b|并非|不是|排除", clause, re.I):
                continue
            if match.start() < heading:
                paragraph = re.split(r"\r?\n\s*\r?\n", text[match.start():], maxsplit=1)[0]
                referenced = re.findall(r"(?:Table|表)\s*([0-9]+)(?![0-9])", paragraph, re.I)
                if not set(caption).intersection(referenced):
                    continue
            types = set(re.split(r"\s*(?:、|，|,|和|与|及|\band\b)\s*", match[1]))
            matched = types.intersection(positions)
            if len(matched) >= 2:
                span = {"start": start + cut + match.start(), "end": start + cut + match.end(), "quote": match[0]}
                output.update(((table, label), span) for label in matched)
    return output


class TablePaths:
    def __init__(self, source, structure):
        self.source = source
        self.tables = {table.table_id: table for table in structure.tables}
        self.cells = {cell.cell_id: cell for table in structure.tables for cell in table.cells}
        self.by_table = defaultdict(list)
        for table in structure.tables:
            self.by_table[table.table_id].extend(table.cells)

    def quote(self, cell):
        return {"start": cell.span.start, "end": cell.span.end,
                "quote": self.source[cell.span.start:cell.span.end], "ref": cell.cell_id}

    def owner_role(self, table, owner, axis):
        """Find the title of the owner axis, keeping its original scope and cell citation."""
        if axis == "identity":
            if owner.column == 0 and owner.row > 0:
                axis = "row"
            elif owner.row == 0 and owner.column > 0:
                axis = "column"
            else:
                return None
        if axis == "row":
            candidates = [c for c in table.cells if c.column == owner.column and c.colspan == owner.colspan
                          and c.row + c.rowspan <= owner.row and (c.row == 0 or c.row in table.header_rows)]
            nearest = max(candidates, key=lambda c: c.row, default=None)
        else:
            candidates = [c for c in table.cells if c.row == owner.row and c.rowspan == owner.rowspan
                          and c.column + c.colspan <= owner.column and (c.column == 0 or c.column in table.row_header_columns)]
            nearest = max(candidates, key=lambda c: c.column, default=None)
        return self.quote(nearest) if nearest is not None else None

    def selected(self, selections, table):
        found = []
        for item in selections:
            cell = self.cells.get(item.get("ref"))
            if cell is None or not table.span.start <= cell.span.start < table.span.end:
                return []
            left, right = item.get("selected_start"), item.get("selected_end")
            if (type(left) is not int or type(right) is not int or not cell.span.start <= left < right <= cell.span.end
                    or self.source[left:right] != item.get("selected_quote")
                    or any(item.get(key) != getattr(cell, key) for key in ("row", "column", "rowspan", "colspan"))):
                return []
            found.append(cell)
        return found

    def _axis(self, owner, value, table):
        if owner == value:
            return "identity"
        for axis, span, traverse, width in (
            ("row", "rowspan", "column", "colspan"), ("column", "colspan", "row", "rowspan")
        ):
            if not _covers(owner, value, axis, span):
                continue
            begin = getattr(owner, traverse) + getattr(owner, width)
            end = getattr(value, traverse)
            if begin > end:
                continue
            # A second text label on the path can start another parameter panel.
            intervening = [c for c in self.by_table[table.table_id] if _covers(c, value, axis, span)
                           and begin <= getattr(c, traverse) < end]
            if all(numeric_cell(self.quote(c)["quote"]) for c in intervening):
                return axis
        return None

    def _relation_axis(self, table, observation, owner, value):
        """Check the three roles in an explicitly cited owner/source/measurement tuple."""
        relations = observation.get("row_relations") or []
        if len(relations) != 1 or relations[0].get("status") != "located" or relations[0].get("alternative"):
            return None, []
        relation = relations[0]
        cited = {(e.get("ref"), e["quote"].strip()) for e in relation["evidence"]}
        for axis, span in (("row", "rowspan"), ("column", "colspan")):
            if getattr(owner, axis) != getattr(value, axis) or owner == value:
                continue
            cells = [c for c in table.cells if _covers(c, value, axis, span)]
            if len(cells) != 3 or any(c.rowspan != 1 or c.colspan != 1 for c in cells):
                continue
            source, = [c for c in cells if c not in (owner, value)]
            if (source.cell_id, relation["target"]) not in cited or self.quote(source)["quote"].strip() != relation["target"]:
                continue
            headers = [self.owner_role(table, c, axis) for c in (owner, source, value)]
            if (any(h is None for h in headers) or (headers[1]["ref"], headers[1]["quote"].strip()) not in cited):
                continue
            roles = [self.cells[h["ref"]] for h in headers]
            labels = {h["quote"].strip().casefold() for h in headers}
            # Separate panels or merged/multiple title bands need scoped verification.
            if (len(labels) != 3 or "" in labels or any(numeric_cell(label) for label in labels)
                    or len({getattr(c, axis) for c in roles}) != 1
                    or any(c.rowspan != 1 or c.colspan != 1 for c in roles)
                    or any(c not in roles for c in table.cells if _covers(c, roles[0], axis, span))):
                continue
            return axis, [self.quote(source), headers[1]]
        return None, []

    def _local_header_band(self, table, owner, value, axis, role):
        """A repeated axis title after data starts a new header band, also in transposed tables."""
        if not role or axis not in {"row", "column"}:
            return None
        cross, width = ("column", "colspan") if axis == "row" else ("row", "rowspan")
        titles = [c for c in table.cells if getattr(c, axis) < getattr(owner, axis)
                  and _covers(c, owner, cross, width) and self.quote(c)["quote"].strip() == role["quote"].strip()]
        if len(titles) < 2:
            return None
        first = min(getattr(c, axis) for c in titles)
        starts = []
        for title in titles:
            pos = getattr(title, axis)
            if pos == first:
                continue
            if (any(getattr(c, axis) == pos and _covers(c, value, cross, width)
                    and not numeric_cell(self.quote(c)["quote"]) for c in table.cells)
                    and any(first < getattr(c, axis) < pos and _covers(c, value, cross, width)
                            and numeric_cell(self.quote(c)["quote"]) for c in table.cells)):
                starts.append(title)
        return max(starts, key=lambda c: getattr(c, axis), default=None)

    def path(self, observation, record, table_id, identity_cells=(), binding_evidence=(), definition=""):
        """Return a single scoped owner path; ambiguous panels and missing cells remain reviewable."""
        table = self.tables.get(table_id)
        if table is None or not table.cells:
            return {"gap": "physical_grid_unavailable"}
        fields = record.get("resolved_fields", {})
        values = self.selected(fields.get("value", []), table)
        if len(values) != 1:
            return {"gap": "value_cell_unresolved"}
        value = values[0]
        context = [{"start": span.start, "end": span.end, "quote": self.source[span.start:span.end]}
                   for span in table.captions + table.footnotes]
        # Heading chains may span several rows or columns. Preserve exact text and location.
        paths = [c for c in table.cells if c != value and
                 ((_covers(c, value, "column", "colspan") and c.row < value.row)
                  or (_covers(c, value, "row", "rowspan") and c.column < value.column))
                 and not numeric_cell(self.quote(c)["quote"])]
        raw = {"headers": [self.quote(c) for c in paths], "context": context, "value": self.quote(value)}
        if table.footnotes:
            return {**raw, "gap": "footnote_scope_requires_review"}
        if record.get("scope") not in {"individual", "group"}:
            return {**raw, "gap": "distribution_scope_requires_review"}
        if record.get("scope") == "individual" and (value.rowspan > 1 or value.colspan > 1):
            return {**raw, "gap": "merged_value_scope_requires_review"}
        declared = fields.get("entity", [])
        owners = self.selected(declared, table)
        inferred = not declared and not observation.get("entity") and record["scope"] == "individual"
        if inferred:
            # Only explicit identity-field cells from *this physical table* are eligible.
            # Require the property on the other axis; a row of numbers alone is ambiguous.
            prop = observation.get("property_name") or observation["record_type"]
            for key in identity_cells:
                identity = self.cells[key]
                axis = self._axis(identity, value, table)
                if axis not in {"row", "column"}:
                    continue
                if any(self._axis(header, value, table) in ({"row", "column"} - {axis})
                       and self.quote(header)["quote"].strip() == prop for header in paths):
                    owners.append(identity)
        if len(owners) != 1:
            return {**raw, "gap": "owner_cell_unresolved"}
        owner = owners[0]
        owner_quote = self.quote(owner)
        combined = None
        if not inferred and owner_quote["quote"].strip() != str(observation.get("entity") or "").strip():
            name = str(observation.get("entity") or "").strip()
            parameter, _, unit_label = owner_quote["quote"].strip().partition("/")
            heading = parameter.removeprefix(name).strip()
            prop = str(observation.get("property_name") or "").strip()
            adjacent = ((owner.row == value.row and owner.column + 1 == value.column)
                        or (owner.column == value.column and owner.row + 1 == value.row))
            if (not name or not parameter.startswith(name) or not heading or heading == parameter
                    or not adjacent or any(c.rowspan != 1 or c.colspan != 1 for c in (owner, value))
                    or not (heading == prop or heading.casefold() in definition.casefold())):
                return {**raw, "gap": "owner_label_unconfirmed"}
            combined = {"subject": name, "property": heading, "unit": unit_label.strip(),
                        "parameter": owner_quote, "table_start": table.span.start}
        axis = self._axis(owner, value, table)
        relation_cells = []
        if record["scope"] == "individual" and observation.get("name_handling") == "related":
            relation_axis, relation_cells = self._relation_axis(table, observation, owner, value)
            axis = relation_axis or axis
        paired_header = None
        if (not axis and record["scope"] == "group" and owner.cell_id in identity_cells
                and observation.get("binding_status") == "supported" and observation.get("target_match") == "supported"
                and any(e["start"] <= owner.span.start and owner.span.end <= e["end"] for e in binding_evidence)):
            # An explicitly bound group can occupy the value of a label/value pair.
            # The next pair gives the property and its reading. Require an independent
            # citation to the complete second pair, including its qualified heading.
            metric = self.selected(fields.get("conditions", []), table)
            if len(metric) == 1 and self.selected(fields.get("unit", []), table) == metric:
                h = metric[0]
                prop, unit = observation.get("property_name"), observation.get("unit")
                label = self.quote(h)["quote"]
                aligned = next((a for a, b in (("row", "column"), ("column", "row"))
                                if getattr(h, a) == getattr(owner, a) == getattr(value, a)
                                and getattr(owner, b) + 1 == getattr(h, b) == getattr(value, b) - 1), None)
                if (aligned and all(c.rowspan == c.colspan == 1 for c in (owner, h, value))
                        and prop and unit and prop in label and str(unit) in label
                        and any(e["start"] <= h.span.start and value.span.end <= e["end"] for e in binding_evidence)):
                    axis, paired_header = aligned, h
        if not axis:
            return {**raw, "gap": "local_owner_path_unconfirmed"}
        role = self.owner_role(table, owner, axis)
        restart = self._local_header_band(table, owner, value, axis, role)
        if restart:
            paths = [cell for cell in paths if getattr(cell, axis) >= getattr(restart, axis)]
            role = self.quote(restart)
        if record.get("scope") == "group":
            scopes = self.selected(record.get("resolved_scope_sources", []), table)
            if owner not in scopes:
                return {**raw, "gap": "collective_scope_unconfirmed"}
        # Explicit field-specific label/unit/condition selections must apply to the value's
        # axis and must not point across a competing side-by-side parameter block.
        cited_headers, qualifier_gap = [], None
        for field in ("unit", "conditions"):
            citations = fields.get(field, [])
            checked = self.selected(citations, table)
            if len(citations) != len(checked):
                qualifier_gap = "qualifier_scope_unconfirmed"
                continue
            for cell in checked:
                if restart and getattr(cell, axis) < getattr(restart, axis):
                    qualifier_gap = "qualifier_outside_local_panel"
                    continue
                vertical = _covers(cell, value, "column", "colspan") and cell.row <= value.row
                horizontal = _covers(cell, value, "row", "rowspan") and cell.column <= value.column
                if not (vertical or horizontal):
                    qualifier_gap = "qualifier_scope_unconfirmed"
                    continue
                nested = (vertical and cell.colspan > 1) or (horizontal and cell.rowspan > 1)
                if cell != value and not nested and self._axis(cell, value, table) is None:
                    qualifier_gap = "qualifier_crosses_intervening_label"
                    continue
                if cell != value and cell != owner:
                    cited_headers.append(cell)
        metric_axis = {"row": "column", "column": "row"}.get(axis)
        metric_headers = [c for c in paths if c != owner and metric_axis and self._axis(c, value, table) == metric_axis]
        # Multi-level parent headers can cover an explicitly selected lower-level header.
        for cell in paths:
            if metric_axis == "column" and cell.colspan > 1:
                if any(_covers(cell, h, "column", "colspan") and cell.row < h.row for h in metric_headers + cited_headers):
                    cited_headers.append(cell)
            if metric_axis == "row" and cell.rowspan > 1:
                if any(_covers(cell, h, "row", "rowspan") and cell.column < h.column for h in metric_headers + cited_headers):
                    cited_headers.append(cell)
        headers = {c.cell_id: self.quote(c) for c in metric_headers + cited_headers}
        if combined:
            headers[owner.cell_id] = owner_quote
        row_group = owner.cell_id
        if axis == "row" and owner.rowspan > 1:
            row_group += f":row-{value.row}"
        elif axis == "column" and owner.colspan > 1:
            row_group += f":column-{value.column}"
        unit_receipt = {"reconciled_unit_ref": paired_header.cell_id} if paired_header and not qualifier_gap else {}
        if combined and combined["unit"] and combined["unit"] == observation.get("unit") and not qualifier_gap:
            unit_receipt = {"reconciled_unit_ref": owner.cell_id}
        relation = {"row_relation_cells": relation_cells} if relation_cells and not qualifier_gap else {}
        if combined:
            relation["combined_parameter"] = combined
        return {**raw, **unit_receipt, **relation, "headers": list(headers.values()), "owner": owner_quote, "axis": axis,
                "owner_role": role, "table_start": table.span.start,
                "scope": record["scope"], "record_group": row_group, "qualifier_gap": qualifier_gap}
