"""Calibrated whole-request admission before claim AND each physical reservation.

These are conservative local estimates, never provider token counts. Retained
mixed CJK/JSON/native-enum requests reached about 1.951 times the old heuristic;
2.2 leaves additional margin. Componentwise counting removes the legacy one-third
script-ratio discontinuity; compacting identical source paths cannot double the
whole-text estimate. The independent CJK floor is unchanged. Financial byte
reservation remains separate.
"""
import json
import math
from copy import deepcopy
from bestseller.services.prompt_compiler import estimate_legacy_prompt_tokens, estimate_prompt_tokens

TOTAL_BUDGET = 65536
INPUT_SAFETY_FACTOR = 2.2
OBSERVED_RATIO_FLOOR = 1.951


class ModelRequestCapacityError(ValueError):
    def __init__(self, receipt):
        self.receipt = deepcopy(receipt)
        super().__init__('model_request_capacity_exceeded:' + json.dumps(receipt, sort_keys=True, separators=(',', ':')))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def measure_request_capacity(messages, *, tools, output_tokens, n_candidates=1, tool_choice=None, total_budget=TOTAL_BUDGET):
    if type(output_tokens) is not int or output_tokens <= 0 or type(n_candidates) is not int or n_candidates <= 0:
        raise ValueError('model_request_capacity_output_reserve_invalid')
    if not isinstance(messages, list) or not messages or any(not isinstance(m, dict) for m in messages):
        raise ValueError('model_request_capacity_messages_invalid')
    text_estimate = 0
    legacy_estimate = 0
    mixed_estimate = 0
    chars = 0
    for message in messages:
        # Include tool-call messages, role framing, structured content and all
        # fields in messages_override; never count only system/user aliases.
        content = message.get('content')
        text = content if isinstance(content, str) else _json(content)
        other = {k: v for k, v in message.items() if k != 'content'}
        extra = _json(other)
        for part in (text, extra):
            legacy_estimate += estimate_legacy_prompt_tokens(part)
            chars += len(part)
            cjk = sum('\u3400' <= ch <= '\u4dbf' or '\u4e00' <= ch <= '\u9fff' or '\uf900' <= ch <= '\ufaff' for ch in part)
            text_estimate += estimate_prompt_tokens(part)
            # Do not let ASCII JSON lower the CJK cost by crossing one global
            # language-ratio threshold. This independent floor is conservative.
            mixed_estimate += math.ceil(cjk * 1.5 + (len(part) - cjk) / 3)
    schema_text = _json({'tools': tools, 'tool_choice': tool_choice}) if tools or tool_choice else ''
    legacy_estimate += estimate_legacy_prompt_tokens(schema_text)
    schema_cjk = sum('\u3400' <= ch <= '\u4dbf' or '\u4e00' <= ch <= '\u9fff' or '\uf900' <= ch <= '\ufaff' for ch in schema_text)
    tool_estimate = estimate_prompt_tokens(schema_text)
    mixed_estimate += math.ceil(schema_cjk * 1.5 + (len(schema_text) - schema_cjk) / 3)
    estimated = text_estimate + tool_estimate
    reserved = max(math.ceil(estimated * INPUT_SAFETY_FACTOR), math.ceil(mixed_estimate * 1.1)) + 512 + 32 * len(messages)
    output_reserve = output_tokens * n_candidates
    return {'schema': 'calibrated-whole-model-request-capacity.v2', 'estimated_input_tokens': estimated,
            'text_tokens': text_estimate, 'tool_tokens': tool_estimate, 'legacy_estimated_input_tokens': legacy_estimate,
            'estimator': 'componentwise-cjk-half-other-quarter; max with independent mixed-script floor', 'mixed_script_input_floor': mixed_estimate,
            'reserved_input_tokens': reserved, 'output_reserve_tokens': output_reserve,
            'effective_max_output_tokens': output_tokens, 'n_candidates': n_candidates,
            'message_count': len(messages), 'message_chars': chars, 'tool_schema_chars': len(schema_text),
            'total_budget': total_budget, 'reserved_total_tokens': reserved + output_reserve,
            'safety_factor': INPUT_SAFETY_FACTOR, 'observed_input_estimator_ratio_floor': OBSERVED_RATIO_FLOOR,
            'budget_passed': reserved + output_reserve <= total_budget,
            'provider_usage_claimed': False, 'financial_reservation_is_not_capacity': True, 'truncated': False}


def assert_effective_request_capacity(payload):
    from bestseller.services.finite_book_budget import current_capacity
    from bestseller.services.model_context_capacity import payload_capacity
    receipt = measure_request_capacity(payload['messages'], tools=payload.get('tools'), tool_choice=payload.get('tool_choice'),
                                       output_tokens=payload['max_tokens'], n_candidates=payload.get('n_candidates', 1),total_budget=current_capacity(model_limit=payload_capacity(payload)))
    if not receipt['budget_passed']:
        raise ModelRequestCapacityError(receipt)
    return receipt
