"""Explicit change-only review views, never a global source retirement."""
from copy import deepcopy
from bestseller.services.conception_snapshots import snapshot_hash

REFINEMENT_AXES = ('no_extra_knowledge_triggers', 'ending_and_source_consistency')
UNCHANGED_AXES = ('core_rule_preservation', 'new_character_initial_states', 'progression_and_costs')


def bind_prior_dependency_review(context, previous, verdict, *, report_id, run_id):
    """Require exact unchanged dependencies; prior negatives remain negatives."""
    if (snapshot_hash(previous) != verdict.get('input_sha256')
            or verdict.get('decision') != 'revise' or not verdict.get('raw_verdict_unchanged')):
        raise ValueError('foundation_dependency_prior_binding')
    for key in ('canonical',):
        if previous['candidate'][key] != context['candidate'][key]:
            raise ValueError('foundation_dependency_source_changed')
    for key in ('book_spec', 'world_spec', 'cast_spec'):
        if previous['candidate']['assets'][key] != context['candidate']['assets'][key]:
            raise ValueError('foundation_dependency_source_changed')
    for key in ('protected_clauses', 'progression_events', 'new_character_cards', 'authorization', 'delivery', 'current_authority_snapshot'):
        if previous[key] != context[key]:
            raise ValueError('foundation_dependency_source_changed')
    checks = {c['axis']: c for c in verdict['checks']}
    if set(checks) != set(UNCHANGED_AXES + REFINEMENT_AXES) or any(checks[a]['verdict'] != 'pass' for a in UNCHANGED_AXES):
        raise ValueError('foundation_dependency_prior_checks')
    out = deepcopy(context)
    out['original'] = deepcopy(previous['candidate'])
    out.pop('prior_invalid_review', None)
    out.pop('previous_candidate_volume', None)
    out['review_axes'] = list(REFINEMENT_AXES)
    out['prior_dependency_review'] = {
        'report_id': report_id, 'run_id': run_id, 'original_decision': 'revise',
        'original_input_sha256': verdict['input_sha256'], 'verdict_sha256': snapshot_hash(verdict),
        'checks': deepcopy(verdict['checks']), 'feedback': verdict['feedback'],
        'unchanged_dependencies_sha256': snapshot_hash({
            'canonical': context['candidate']['canonical'],
            'assets': {k: context['candidate']['assets'][k] for k in ('book_spec', 'world_spec', 'cast_spec')},
            **{k: context[k] for k in ('protected_clauses','progression_events','new_character_cards','authorization','delivery','current_authority_snapshot')},
        }),
        'not_a_new_grade': True, 'scope': 'Only the two remaining axes are newly reviewed; original three passes remain bound to exact unchanged dependencies.',
    }
    return out


def _before_patches(old, current, path=''):
    if old == current:
        return []
    if isinstance(old, dict) and isinstance(current, dict):
        result = []
        for key in sorted(set(old) | set(current)):
            child = path + '/' + key.replace('~','~0').replace('/','~1')
            if key not in old:
                result.append({'path': child, 'old_exists': False})
            elif key not in current:
                result.append({'path': child, 'old_exists': True, 'old_value': deepcopy(old[key])})
            else:
                result.extend(_before_patches(old[key], current[key], child))
        return result
    if isinstance(old, list) and isinstance(current, list) and len(old) == len(current):
        return [r for i,(a,b) in enumerate(zip(old,current)) for r in _before_patches(a,b,path+'/'+str(i))]
    return [{'path': path, 'old_exists': True, 'old_value': deepcopy(old)}]


def restore_original(current, patches):
    out = deepcopy(current)
    for patch in patches:
        if patch['path'] == '':
            out = deepcopy(patch['old_value'])
            continue
        parts = [p.replace('~1','/').replace('~0','~') for p in patch['path'][1:].split('/')]
        parent = out
        for key in parts[:-1]:
            parent = parent[int(key)] if isinstance(parent,list) else parent[key]
        key = int(parts[-1]) if isinstance(parent,list) else parts[-1]
        if patch['old_exists']:
            parent[key] = deepcopy(patch['old_value'])
        else:
            del parent[key]
    return out


def dependency_review_view(context):
    """Full current operative source once, exact changed old values, archive refs.

    Only this explicitly bounded seal/knowledge/ending review excludes historical
    assessment/packaging payload and transition audit. Commercial briefs, unknown
    authored fields, rules and character knowledge remain complete. These archives
    remain live for their own packaging/audit consumers, not globally superseded.
    """
    axes = tuple(context.get('review_axes', ()))
    if not axes or not set(axes).issubset(REFINEMENT_AXES):
        raise ValueError('foundation_dependency_scope_not_declared')
    scoped = deepcopy(context)
    archives = []
    for side in ('original','candidate'):
        canonical = scoped[side]['canonical']
        for parent, key, path in (
            (canonical, '_snapshot_audit', '/canonical/_snapshot_audit'),
            (canonical.get('conception_artifacts',{}), 'story_appeal', '/canonical/conception_artifacts/story_appeal'),
        ):
            if key not in parent:
                continue
            value = parent.pop(key)
            record = {'path': '/'+side+path, 'sha256': snapshot_hash(value),
                'reason': 'Historical evaluation/packaging or transition audit; excluded only from this seal/knowledge/ending dependency review, not retired globally.'}
            archives.append(record)
            parent[key] = {'scoped_archive_sha256': record['sha256']}
    prior = scoped['prior_dependency_review']
    for check in prior.get('checks', []):
        # Preserve all decisions and reasons. Exact quote copies stay in the
        # immutable prior report; this review has its own literal source catalog.
        for key in ('source_quote', 'candidate_quote'):
            check.pop(key, None)
    patches = _before_patches(scoped['original'],scoped['candidate'])
    if restore_original(scoped['candidate'],patches) != scoped['original']:
        raise ValueError('foundation_dependency_before_roundtrip')
    view = {k:v for k,v in scoped.items() if k not in ('original','candidate')}
    view.update(current_candidate=scoped['candidate'], original_before_patches=patches,
        source_archives=archives, scope_receipt={
            'schema': 'foundation-dependency-source-view.v1',
            'full_retained_context_sha256': snapshot_hash(context),
            'scoped_original_sha256': snapshot_hash(scoped['original']),
            'scoped_candidate_sha256': snapshot_hash(scoped['candidate']),
            'before_roundtrip_verified': True, 'whole_book_review': False,
            'all_original_source_retained': True,
        })
    return view


def dependency_review_fragments(context):
    """One actual dependency, literal required source values and old/new scenes.

    Source fragments are ordinary JSON values, not a codec or a whole-book
    review. Full original context stays retained and its hash binds this view.
    """
    from bestseller.services.retained_planning_clauses import resolve_pointer
    axes = tuple(context.get('review_axes', ()))
    if len(axes) != 1 or axes[0] not in REFINEMENT_AXES or not context.get('prior_dependency_review'):
        raise ValueError('foundation_dependency_single_axis_required')
    paths = ['/candidate/assets/book_spec', '/candidate/assets/world_spec',
        '/candidate/assets/cast_spec', '/candidate/canonical/premise',
        '/candidate/canonical/synopsis', '/candidate/canonical/hook_card',
        '/candidate/canonical/story_spine', '/candidate/canonical/seriality_proof',
        '/candidate/canonical/writing_profile', '/candidate/canonical/commercial_brief',
        '/candidate/canonical/conception_artifacts/commercial_brief',
        '/candidate/canonical/concept_contract/story_architecture',
        '/protected_clauses', '/authorization', '/delivery', '/progression_events',
        '/new_character_cards',
        '/original/assets/book_spec/series_engine', '/review_disagreement_and_corrections']
    # Do not reintroduce full canonical blobs through an axis label. These fields
    # are the actual information-flow or closure dependencies of each scene.
    fields = ['volume_goal', 'opening_state', 'key_reveals', 'volume_resolution']
    if axes[0] == 'ending_and_source_consistency':
        fields += ['volume_climax', 'reader_hook_to_next', 'foreshadowing_paid_off',
                   'foreshadowing_planted', 'volume_obstacle']
        # The full existing climax literally contains present/action/information/
        # trigger values of EVERY old/current scene. Select those exact original
        # source strings once, plus every location that the rendering lacks.
        # This is not a new codec: no source value is rewritten or reconstructed
        # for the judge, and the complete source climax is still literal text.
        for side in ('original', 'candidate'):
            volume = context[side]['assets']['volume_plan'][0]
            cards = volume['information_flow_scene_cards']
            rendered = '\n'.join(f"第{c['chapter']}章｜{c['present']}｜{c['actual_action']}｜{c['actual_information']}｜{c['trigger_account']}" for c in cards)
            if not volume['volume_climax'].endswith('\n收尾实际信息流：'+rendered):
                raise ValueError('foundation_dependency_scene_rendering_not_exact')
            paths += [f'/{side}/assets/volume_plan/0/information_flow_scene_cards/{i}/location' for i in range(len(cards))]
        # Entity-ID registry has no new scene knowledge; unchanged identity is
        # already bound by the prior dependency proof. Complete character/Book
        # bodies remain inline, and all explicit creator contracts stay inline.
        paths += ['/current_authority_snapshot/'+key for key in context['current_authority_snapshot'] if key != 'entity_registry']
    else:
        fields += ['information_flow_scene_cards']
        paths += ['/current_authority_snapshot']
    for side in ('original', 'candidate'):
        paths += [f'/{side}/assets/volume_plan/0/{key}' for key in fields]
    # Known packaging/evaluation/provenance fields are not story authority in
    # this bounded review. Unknown authored additions remain literal source.
    known = {'tags','title','premise','synopsis','hook_card','hook_spec','attempt_id',
        'input_hash','story_spine','schema_version','_snapshot_audit','conception_mode',
        'seriality_proof','writing_profile','commercial_brief','concept_contract',
        'conception_artifacts','creation_intent_hash','creator_source_revision',
        'retained_planning_clauses','configuration_revision_source'}
    for key in context['candidate']['canonical'].keys() - known:
        paths.append('/candidate/canonical/'+key.replace('~','~0').replace('/','~1'))
    fragments = [{'pointer': p, 'value': deepcopy(resolve_pointer(context,p))} for p in paths]
    prior = deepcopy(context['prior_dependency_review'])
    for check in prior['checks']:
        for key in ('source_quote','candidate_quote'):
            check.pop(key, None)
    return {'schema':'foundation-dependency-fragments.v1', 'review_axes':list(axes),
        'source_fragments':fragments, 'prior_dependency_review':prior,
        'source_binding':{'full_retained_context_sha256':snapshot_hash(context),
            'current_canonical_sha256':snapshot_hash(context['candidate']['canonical']),
            'current_assets_sha256':snapshot_hash(context['candidate']['assets']),
            'old_assets_sha256':snapshot_hash(context['original']['assets']),
            'whole_book_review':False,
            'scope':'Only the declared scene dependency; unchanged design/style, packaging and methodology evaluations are not re-reviewed. Entire World/Cast, historical knowledge, affected original/current scene values, canonical profiles including contradictions, full architecture and both commercial briefs remain literal.'}}
