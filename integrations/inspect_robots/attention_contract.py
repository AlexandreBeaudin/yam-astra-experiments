"""Schema and validation for model-reported spatial attention, not internal saliency."""
import math

VIEWS = ('main', 'left', 'right')


def attention_schema():
    number = {'type': 'number', 'minimum': 0., 'maximum': 1.}
    region = {'type': 'object', 'additionalProperties': False,
        'required': ['label', 'center_uv', 'box_uv', 'weight'],
        'properties': {
            'label': {'type': 'string', 'minLength': 1},
            'center_uv': {'type': 'array', 'items': number, 'minItems': 2, 'maxItems': 2},
            'box_uv': {'type': 'array', 'items': number, 'minItems': 4, 'maxItems': 4},
            'weight': {'type': 'number', 'exclusiveMinimum': 0., 'maximum': 1.}}}
    def view_schema():
        return {'anyOf': [
            {'type': 'object', 'additionalProperties': False, 'required': ['visible', 'regions'],
             'properties': {'visible': {'type': 'boolean', 'enum': [True]},
                'regions': {'type': 'array', 'items': region, 'minItems': 1, 'maxItems': 3}}},
            {'type': 'object', 'additionalProperties': False, 'required': ['visible', 'regions'],
             'properties': {'visible': {'type': 'boolean', 'enum': [False]},
                'regions': {'type': 'array', 'items': region, 'maxItems': 0}}}]}
    return {'type': 'object', 'additionalProperties': False,
        'required': ['primary_view', 'fusion_mode', 'views'],
        'description': 'Self-reported attended regions in full original images; coordinates normalized from top-left. Weights sum to one per visible view; masked views are false/empty.',
        'properties': {
            'primary_view': {'type': 'string', 'enum': list(VIEWS)},
            'fusion_mode': {'type': 'string', 'enum': ['single_view', 'cross_view_parallel']},
            'views': {'type': 'object', 'additionalProperties': False, 'required': list(VIEWS),
                'properties': {v: view_schema() for v in VIEWS}}}}


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f'{label} requires exactly {", ".join(expected)}')


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    if not (0 < value <= 1 if positive else 0 <= value <= 1):
        raise ValueError(f'{label} must be in {"(0, 1]" if positive else "[0, 1]"}')


def validate_attention(value, *, masked_views=()):
    _keys(value, ['primary_view', 'fusion_mode', 'views'], 'attention')
    if value['primary_view'] not in VIEWS:
        raise ValueError('primary_view must be main, left or right')
    if value['fusion_mode'] not in ('single_view', 'cross_view_parallel'):
        raise ValueError('invalid fusion_mode')
    _keys(value['views'], VIEWS, 'views')
    visible = []
    for view in VIEWS:
        item = value['views'][view]
        _keys(item, ['visible', 'regions'], view)
        if type(item['visible']) is not bool or not isinstance(item['regions'], list):
            raise ValueError(f'{view}: visible must be boolean and regions must be an array')
        regions = item['regions']
        if not item['visible']:
            if regions: raise ValueError(f'{view}: invisible or masked views require regions=[]')
            continue
        if view in masked_views:
            raise ValueError(f'{view} is masked; use visible=false and regions=[]')
        if not 1 <= len(regions) <= 3:
            raise ValueError(f'{view}: visible views require 1 to 3 regions')
        visible.append(view)
        for r in regions:
            _keys(r, ['label', 'center_uv', 'box_uv', 'weight'], 'region')
            if not isinstance(r['label'], str) or not r['label'].strip():
                raise ValueError('region label must be a nonempty string')
            for name, n in [('center_uv', 2), ('box_uv', 4)]:
                coords = r[name]
                if not isinstance(coords, list) or len(coords) != n:
                    raise ValueError(f'{name} must contain {n} numbers')
                for v in coords: _number(v, name)
            _number(r['weight'], 'weight', positive=True)
            x0, y0, x1, y1 = r['box_uv']; u, v = r['center_uv']
            if not (x0 < x1 and y0 < y1 and x0 <= u <= x1 and y0 <= v <= y1):
                raise ValueError('box_uv must be ordered with positive area and contain center_uv')
        if not math.isclose(sum(r['weight'] for r in regions), 1., rel_tol=0, abs_tol=1e-6):
            raise ValueError(f'{view}: region weights must sum to 1')
    if value['primary_view'] not in visible:
        raise ValueError('primary_view must be visible and unmasked')
    if value['fusion_mode'] == 'cross_view_parallel' and len(visible) < 2:
        raise ValueError('cross_view_parallel requires at least two visible views')
    return value
