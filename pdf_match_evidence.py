"""Bounded KD-PE-100 evidence shared by the worker producer and parent."""
import math


MAX_MATCHES = 5
MAX_LOCATIONS = 3
MAX_BOXES = 4


def _number(value):
    return type(value) in (int, float) and -1e9 < value < 1e9 and math.isfinite(value)


def _page_size(size):
    return (isinstance(size, (tuple, list)) and len(size) == 2
            and all(_number(value) and value > 0 for value in size))


def _box_in_page(box, size):
    return (isinstance(box, (tuple, list)) and len(box) == 4
            and all(_number(value) for value in box)
            and 0 <= box[0] < box[2] <= size[0]
            and 0 <= box[1] < box[3] <= size[1])


def source(document, page, row=None, *, size=(), boxes=()):
    location = {"document": document, "pdf_page": page}
    if row is not None:
        location["table_row"] = row
    valid = sorted({tuple(box) for box in boxes if _page_size(size) and _box_in_page(box, size)})
    if valid:
        location["page_size_pt"] = list(size)
        location["boxes_pt"] = [list(box) for box in valid[:MAX_BOXES]]
    return location, len(valid) > MAX_BOXES


def location_key(location):
    return location["pdf_page"], location.get("table_row") or 0


def _valid_location(location, known_files, expected_document, allow_row):
    if not isinstance(location, dict):
        return False
    keys = {"document", "pdf_page", "page_size_pt", "boxes_pt"}
    if allow_row:
        keys.add("table_row")
    document, page = location.get("document"), location.get("pdf_page")
    if (location.keys() - keys or not isinstance(document, str)
            or document not in known_files or document != expected_document
            or type(page) is not int or page < 1):
        return False
    if "table_row" in location and (type(location["table_row"]) is not int or location["table_row"] < 1):
        return False
    if "boxes_pt" not in location:
        return "page_size_pt" not in location
    size, boxes = location.get("page_size_pt"), location["boxes_pt"]
    return (isinstance(size, list) and _page_size(size)
            and isinstance(boxes, list) and 1 <= len(boxes) <= MAX_BOXES
            and all(isinstance(box, list) and _box_in_page(box, size) for box in boxes)
            and [tuple(box) for box in boxes] == sorted({tuple(box) for box in boxes}))


def valid_evidence(evidence, known_files, element_list):
    if (not isinstance(evidence, dict)
            or evidence.keys() != {"schema_version", "kind", "matches", "truncated"}
            or type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1
            or evidence["kind"] != "pdf_text_match" or type(evidence["truncated"]) is not bool):
        return False
    matches = evidence["matches"]
    if not isinstance(matches, list) or not 1 <= len(matches) <= MAX_MATCHES:
        return False
    # Import here because the cross-check uses the evidence builder.
    from kd_crosscheck import designation_type, pair_key
    designations = []
    scheme = None
    for match in matches:
        if (not isinstance(match, dict) or match.keys() != {"designation", "scheme", "element_list"}
                or not isinstance(match["designation"], str) or not 1 <= len(match["designation"]) <= 100):
            return False
        designations.append(match["designation"])
        for role in ("scheme", "element_list"):
            locations = match[role]
            if not isinstance(locations, list) or not 1 <= len(locations) <= MAX_LOCATIONS:
                return False
            if role == "scheme" and scheme is None:
                scheme = locations[0].get("document") if isinstance(locations[0], dict) else None
            document = scheme if role == "scheme" else element_list
            if not all(_valid_location(item, known_files, document, role == "element_list")
                       for item in locations):
                return False
            keys = [location_key(item) for item in locations]
            if keys != sorted(set(keys)):
                return False
    return (designations == sorted(set(designations))
            and designation_type(scheme) == "Э3" and designation_type(element_list) == "ПЭ3"
            and pair_key(scheme) == pair_key(element_list))
