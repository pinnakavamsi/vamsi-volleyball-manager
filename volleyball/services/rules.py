from copy import deepcopy
from . import __name__ as _package
from ..common import APIError

DEFAULT_RULES = {
 'mode':'best_of','best_of':3,'fixed_sets':3,'sets_to_win':2,
 'regular_target':25,'deciding_target':15,'win_by':2,'cap':None,'timeouts':2,
 'draw_allowed':False,'win_points':2,'loss_points':0,'draw_points':1,
 'forfeit_win_points':2,'forfeit_loss_points':0,'include_forfeit_scores':False,
 'tie_breaks':['standings_points','won','head_to_head','set_quotient','point_quotient'],
 'head_to_head':'two_team','restart_tie_breaks':True,'quotient_zero':'infinity',
 'cross_pool':'points_per_match','automatic_approval':False,
}
CRITERIA={'standings_points','won','win_percentage','head_to_head','set_difference','set_quotient','point_difference','point_quotient','manual','playoff'}

def validate_rules(value,partial=False):
    if not isinstance(value,dict): raise APIError('Rules must be an object.')
    value=deepcopy(value)
    aliases={'target_points':'regular_target','regular_points':'regular_target','deciding_points':'deciding_target','win_by_margin':'win_by','point_cap':'cap','sets':'fixed_sets'}
    for old,new in aliases.items():
        if old in value: value[new]=value.pop(old)
    unknown=set(value)-set(DEFAULT_RULES)
    if unknown: raise APIError('Unknown rule fields: '+', '.join(sorted(unknown)))
    rules=deepcopy(DEFAULT_RULES);rules.update(value)
    for key in ('draw_allowed','include_forfeit_scores','restart_tie_breaks','automatic_approval'):
        if not isinstance(rules[key],bool): raise APIError(f'{key} must be true or false.')
    for key in ('best_of','fixed_sets','sets_to_win','regular_target','deciding_target','win_by','timeouts'):
        if not isinstance(rules[key],int) or isinstance(rules[key],bool) or rules[key] < (0 if key=='timeouts' else 1):
            raise APIError(f'{key} must be a valid positive integer (timeouts may be zero).')
    if rules['mode'] not in ('best_of','fixed'): raise APIError('Choose best_of or fixed sets.')
    if rules['mode']=='best_of':
        if rules['best_of']%2!=1 or rules['best_of']>9: raise APIError('Best of must be an odd number no greater than 9.')
        if 'sets_to_win' not in value: rules['sets_to_win']=rules['best_of']//2+1
        if rules['sets_to_win']!=rules['best_of']//2+1: raise APIError('Sets needed to win must be a majority of best of.')
    if rules['fixed_sets']>9: raise APIError('Fixed set count must be at most 9.')
    if rules['mode']=='fixed' and rules['fixed_sets']%2==0 and not rules['draw_allowed']:
        raise APIError('An even fixed set count requires an explicit drawn-match policy.')
    if rules['cap'] is not None and (not isinstance(rules['cap'],int) or isinstance(rules['cap'],bool) or rules['cap']<max(rules['regular_target'],rules['deciding_target'])):
        raise APIError('Hard cap must be at least the set targets; at the cap a one point lead awards the set.')
    if rules['regular_target']>200 or rules['deciding_target']>200 or rules['win_by']>20 or rules['timeouts']>10:
        raise APIError('Scoring configuration exceeds supported V1 limits.')
    for key in ('win_points','loss_points','draw_points','forfeit_win_points','forfeit_loss_points'):
        if not isinstance(rules[key],int) or isinstance(rules[key],bool) or abs(rules[key])>100: raise APIError(f'{key} must be an integer between -100 and 100.')
    if not isinstance(rules['tie_breaks'],list) or not rules['tie_breaks'] or any(not isinstance(item,str) for item in rules['tie_breaks']) or len(set(rules['tie_breaks']))!=len(rules['tie_breaks']) or set(rules['tie_breaks'])-CRITERIA:
        raise APIError('Choose a unique ordered list of supported tie-break criteria.')
    if rules['head_to_head'] not in ('two_team','mini_table') or rules['quotient_zero'] not in ('infinity','zero'):
        raise APIError('Select an explicit head-to-head and zero-denominator quotient policy.')
    if rules['cross_pool'] not in ('points_per_match','total_points'): raise APIError('Select a cross-pool comparison policy.')
    return {k:rules[k] for k in value} if partial else rules

def effective_rules(tournament,division=None,stage=None,match=None):
    rules=deepcopy(DEFAULT_RULES)
    for obj in (tournament,division,stage,match):
        if obj is not None: rules.update(obj.rules or {})
    return validate_rules(rules)

def set_winner(a,b,rules,set_index):
    maximum=rules['best_of'] if rules['mode']=='best_of' else rules['fixed_sets']
    target=rules['deciding_target'] if set_index==maximum-1 else rules['regular_target']
    if a==b: return None
    top=max(a,b)
    capped=rules['cap'] is not None and top>=rules['cap']
    if capped or (top>=target and abs(a-b)>=rules['win_by']): return 'a' if a>b else 'b'
    return None

def match_winner(state,rules):
    sets=state.get('sets',[])
    wins={side:sum(1 for s in sets if s.get('complete') and s.get('winner')==side) for side in ('a','b')}
    if rules['mode']=='best_of':
        for side in ('a','b'):
            if wins[side]>=rules['sets_to_win']: return side
        return None
    if len(sets)==rules['fixed_sets'] and all(s.get('complete') for s in sets):
        return 'draw' if wins['a']==wins['b'] else ('a' if wins['a']>wins['b'] else 'b')
    return None
