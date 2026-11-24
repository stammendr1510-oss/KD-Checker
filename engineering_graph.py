"""Electrical connection graph analysis (KD-PE-110).

Pure, deterministic, stdlib-only. Nodes are normalized (device, terminal)
endpoints; edges come ONLY from validated structured Connection records —
electrical topology is never inferred from raw PDF text. Designation-agnostic:
no rule special-cases any reference class, cabinet, or sheet.
"""
from __future__ import annotations
import re

from engine import issue
from naming import normalized
from connection_checks import Connection, Endpoint

MAX_EXAMPLES = 8

CODE_DANGLING = 'KD-EM-110-1'
CODE_LOOP = 'KD-EM-110-2'
CODE_COUNT = 'KD-EM-110-3'

# Length units (Cyrillic + Latin spellings) marking bulk-measure rows; only
# piece-counted PE3 rows are comparable with distinct wire counts.
_LENGTH_UNITS = re.compile(
    r'(?<![0-9A-Za-zА-Яа-яЁё])(мм|км|м|mm|km|m)(?![0-9A-Za-zА-Яа-яЁё])',
    re.IGNORECASE)


def connections_from_rows(rows, *, document, sheet='', project='', revision='') -> list:
    """Map validated, header-normalized workbook rows to graph connections."""
    records = []
    for index, row in enumerate(rows, 2):
        required = ('source_device', 'source_terminal', 'target_device',
                    'target_terminal', 'wire')
        if not all(str(row.get(key, '') or '').strip() for key in required):
            continue
        location = f'{sheet}, строка {index}' if sheet else f'строка {index}'
        records.append(Connection(
            project=str(row.get('project') or project or document),
            revision=str(row.get('revision') or revision or 'не указана'),
            wire=str(row['wire']).strip(), cable=str(row.get('cable') or '').strip(),
            source=Endpoint(device=str(row['source_device']).strip(),
                            terminal=str(row['source_terminal']).strip()),
            target=Endpoint(device=str(row['target_device']).strip(),
                            terminal=str(row['target_terminal']).strip()),
            document=document, location=location,
        ))
    return records


def _node_key(endpoint) -> tuple:
    return (normalized(endpoint.device), normalized(endpoint.terminal))


def build_graph(connections) -> dict:
    """Undirected adjacency {node: frozenset(neighbours)} from validated records.

    Deterministic: nodes and neighbour sets are sorted on output. Self
    connections still link the node to itself so the device counts as wired.
    """
    adjacency = {}
    for record in connections:
        a = _node_key(record.source)
        b = _node_key(record.target)
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    return {node: frozenset(sorted(neighbours))
            for node, neighbours in sorted(adjacency.items())}


def verify_dangling_devices(model) -> list:
    """KD-EM-110-1: Э3/ПЭ3 devices with no structured connection at all.

    Only evaluated when structured connections are provided; absence of edges
    may simply mean partial export coverage.
    """
    if not model.connections:
        return []
    wired = {device for device, _terminal in build_graph(model.connections)}
    dangling = sorted(name for name in model.devices if name not in wired)
    if not dangling:
        return []
    examples = dangling[:MAX_EXAMPLES]
    places = '; '.join(f'{name} ({_device_place(model, name)})' for name in examples)
    first = model.devices[examples[0]].provenance
    ref = first[0] if first else None
    return [issue('info', CODE_DANGLING, ref.document if ref else '', ref.describe() if ref else '',
        f'{len(dangling)} обозначений из Э3/ПЭ3 не имеют ни одного структурированного соединения '
        f'(примеры: {places}). Это не подтверждённая ошибка КД: экспорт соединений может быть неполным.',
        'Сверьте полноту структурированного экспорта соединений с исходными Э3/ПЭ3')]


def _device_place(model, name: str) -> str:
    dev = model.devices.get(name)
    if dev is not None and dev.provenance:
        return dev.provenance[0].describe()
    return ''


def verify_wire_loops(model) -> list:
    """KD-EM-110-2: one wire label forming a ring through 3+ contacts.

    Undirected per-wire rings only: exact repeats and reverse re-exports of
    the same pair are KD-CON-101 territory, direct source==target is
    KD-CON-102; a genuine ring needs at least a triangle.
    """
    wires = {}
    first_seen = {}
    for record in model.connections:
        if record.source == record.target:
            continue
        a = _node_key(record.source)
        b = _node_key(record.target)
        adjacency = wires.setdefault(record.wire, {})
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
        first_seen.setdefault(record.wire, record)
    out = []
    for wire in sorted(wires):
        ring = _find_ring(wires[wire])
        if ring is None:
            continue
        record = first_seen[wire]
        chain = ' → '.join(f'{device}:{terminal}' for device, terminal in ring)
        out.append(issue('warning', CODE_LOOP, record.document, record.location,
            f'Провод «{wire}» образует замкнутый контур через контакты {chain}. '
            'Это не подтверждённая ошибка КД; возможны шлейфы или повтор экспорта.',
            'Сверьте топологию провода в структурированном экспорте; короткое замыкание не подтверждено'))
        if len(out) >= MAX_EXAMPLES:
            break
    return out


def _find_ring(adjacency) -> list | None:
    """First undirected cycle via deterministic iterative DFS, or None."""
    color = {node: 0 for node in adjacency}
    for start in sorted(adjacency):
        if color[start]:
            continue
        color[start] = 1
        stack = [(start, None, iter(sorted(adjacency[start])))]
        path = [start]
        while stack:
            node, parent, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt == parent:
                    continue
                state = color[nxt]
                if state == 1:
                    return path[path.index(nxt):]
                if state == 0:
                    color[nxt] = 1
                    stack.append((nxt, node, iter(sorted(adjacency[nxt]))))
                    path.append(nxt)
                    advanced = True
                    break
            if not advanced:
                color[node] = 2
                stack.pop()
                path.pop()
    return None


def verify_wire_counts(model) -> list:
    """KD-EM-110-3: distinct wire count per cable vs PE3 row quantity.

    Only piece-counted rows are compared; rows measured in length units
    (м, мм, км, m, ...) are skipped so bulk cable length never triggers
    false positives.
    """
    if not model.connections or not model.spec_rows:
        return []
    cable_wires = {}
    for record in model.connections:
        if record.cable:
            cable_wires.setdefault(normalized(record.cable), set()).add(normalized(record.wire))
    if not cable_wires:
        return []
    out = []
    for row in model.spec_rows:
        if row.quantity is None or _LENGTH_UNITS.search(row.raw_text or ''):
            continue
        for des in row.designations:
            wires = cable_wires.get(des.normalized)
            if wires is None or len(wires) == row.quantity:
                continue
            ref = row.provenance
            place = ref.describe() if ref else 'Перечень элементов'
            out.append(issue('info', CODE_COUNT, ref.document if ref else '', place,
                f'Для кабеля «{des.raw}» в структурированном экспорте {len(wires)} различных проводов, '
                f'в строке ПЭ3 указано количество {row.quantity} («{row.raw_text}»). Это не подтверждённая ошибка КД.',
                'Сверьте количество жил/проводов кабеля с исходной строкой перечня и экспортом'))
            break
        if len(out) >= MAX_EXAMPLES:
            break
    return out


def run_graph_verifiers(model) -> list:
    return (verify_dangling_devices(model)
            + verify_wire_loops(model)
            + verify_wire_counts(model))
