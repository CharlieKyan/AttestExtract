"""Verify local cell paths against the physical grid of the original source."""

from collections import defaultdict
import re


def _covers(cell, value, axis, span):
    return (getattr(cell, axis) <= getattr(value, axis)
            and getattr(cell, axis) + getattr(cell, span) >= getattr(value, axis) + getattr(value, span))


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
            if all(re.fullmatch(r"[\d\s.,%±+−<>~–—-]+", self.quote(c)["quote"]) for c in intervening):
                return axis
        return None

    def path(self, observation, record, table_id, identity_cells=()):
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
                 and not re.fullmatch(r"[\d\s.,%±+−<>~–—-]+", self.quote(c)["quote"])]
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
        if not inferred and owner_quote["quote"].strip() != str(observation.get("entity") or "").strip():
            return {**raw, "gap": "owner_label_unconfirmed"}
        axis = self._axis(owner, value, table)
        if not axis:
            return {**raw, "gap": "local_owner_path_unconfirmed"}
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
        row_group = owner.cell_id
        if axis == "row" and owner.rowspan > 1:
            row_group += f":row-{value.row}"
        elif axis == "column" and owner.colspan > 1:
            row_group += f":column-{value.column}"
        return {**raw, "headers": list(headers.values()), "owner": owner_quote, "axis": axis,
                "owner_role": self.owner_role(table, owner, axis),
                "scope": record["scope"], "record_group": row_group, "qualifier_gap": qualifier_gap}
