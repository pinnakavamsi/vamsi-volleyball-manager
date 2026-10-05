from collections import defaultdict
from sqlalchemy import select
from ..db import get_db
from ..models import Entry,Match,Tournament,Stage
from .rules import effective_rules


def standings(division,stage_id=None,provisional=False,pool=None):
    db=get_db(); t=db.get(Tournament,division.tournament_id)
    stage=db.get(Stage,stage_id) if stage_id else None
    rules=effective_rules(t,division,stage)
    entries=list(db.scalars(select(Entry).where(Entry.division_id==division.id,Entry.status=='active')))
    if pool is not None: entries=[e for e in entries if e.pool==pool]
    rows={e.id:{'entry_id':e.id,'team_id':e.team_id,'name':e.name,'seed':e.seed,'played':0,'won':0,'lost':0,'drawn':0,'sets_won':0,'sets_lost':0,'points_for':0,'points_against':0,'standings_points':0} for e in entries}
    q=select(Match).where(Match.division_id==division.id,Match.result_status.in_(['finalized','submitted'] if provisional else ['finalized']))
    if stage_id: q=q.where(Match.stage_id==stage_id)
    if pool is not None:q=q.where(Match.pool==pool)
    matches=[m for m in db.scalars(q) if m.entry_a_id in rows and m.entry_b_id in rows and m.outcome not in ('bye','cancelled','abandoned')]
    stage_policies={s.id:effective_rules(t,division,s) for s in db.scalars(select(Stage).where(Stage.division_id==division.id))}
    def accumulate(target,m):
        if m.entry_a_id not in target or m.entry_b_id not in target:return
        a,b=target[m.entry_a_id],target[m.entry_b_id]
        awards=stage_policies.get(m.stage_id,rules)
        for side,r,other in [('a',a,b),('b',b,a)]:
            r['played']+=1
            if m.winner_id==r['entry_id']:
                r['won']+=1;r['standings_points']+=awards['forfeit_win_points'] if m.outcome=='forfeit' else awards['win_points']
            elif m.winner_id:
                r['lost']+=1;r['standings_points']+=awards['forfeit_loss_points'] if m.outcome=='forfeit' else awards['loss_points']
            else:r['drawn']+=1;r['standings_points']+=awards['draw_points']
            if m.outcome!='forfeit' or awards['include_forfeit_scores']:
                for s in m.state.get('sets',[]):
                    if not s.get('complete'):continue
                    r['sets_won']+=int(s.get('winner')==side);r['sets_lost']+=int(s.get('winner') not in (None,side))
                    r['points_for']+=s[side];r['points_against']+=s['b' if side=='a' else 'a']
    for m in matches:accumulate(rows,m)
    def quotient(a,b):return a/b if b else (float('inf') if a and rules['quotient_zero']=='infinity' else 0)
    for r in rows.values():
        r.update(set_difference=r['sets_won']-r['sets_lost'],point_difference=r['points_for']-r['points_against'],
            set_quotient=quotient(r['sets_won'],r['sets_lost']),point_quotient=quotient(r['points_for'],r['points_against']),
            win_percentage=r['won']/r['played'] if r['played'] else 0)
    criteria=rules['tie_breaks']
    def rank_group(group,index=0):
        if len(group)<2:return [group]
        if index>=len(criteria):return [group]
        criterion=criteria[index]
        if criterion in ('manual','playoff'):
            resolution=(division.settings or {}).get('tie_resolution',{})
            if criterion=='manual' and resolution.get('reason') and all(r['entry_id'] in resolution.get('order',[]) for r in group):
                return [[r] for r in sorted(group,key=lambda r:resolution['order'].index(r['entry_id']))]
            return rank_group(group,index+1)
        if criterion=='head_to_head':
            if rules['head_to_head']=='two_team' and len(group)!=2:return rank_group(group,index+1)
            mini={r['entry_id']:{k:(r[k] if k in ('entry_id','name') else 0) for k in ('entry_id','name','played','won','lost','drawn','standings_points','sets_won','sets_lost','points_for','points_against')} for r in group}
            for m in matches:accumulate(mini,m)
            values={k:r['standings_points'] for k,r in mini.items()}
        else:values={r['entry_id']:r[criterion] for r in group}
        buckets=defaultdict(list)
        for r in group:buckets[values[r['entry_id']]].append(r)
        if len(buckets)==1:return rank_group(group,index+1)
        result=[]
        for value in sorted(buckets,reverse=True):
            subset=buckets[value]
            restart=criterion=='head_to_head' and rules['restart_tie_breaks']
            result+=rank_group(subset,0 if restart else index+1)
        return result
    ordered=[];rank=1
    for group in rank_group(list(rows.values())):
        for r in sorted(group,key=lambda r:r['seed']):
            r.update(rank=rank,tied=len(group)>1,explanation='Unresolved tie; displayed seed does not break the tie.' if len(group)>1 else 'Ranked by the configured ordered criteria.')
            for key in ('set_quotient','point_quotient'):
                if r[key]==float('inf'):r[key]='∞'
            ordered.append(r)
        rank+=len(group)
    return {'division_id':division.id,'stage_id':stage_id,'rows':ordered,'policy':rules,'generation':t.standings_generation,
        'official':not provisional,'sample_size':len(matches),'explanation':'Official finalized results only.' if not provisional else 'Provisional preview includes submitted results.'}


def compare_pools(rows,rules,resolution=None):
    """Comparable qualification metrics for pools with unequal games played.

    Head-to-head cannot decide comparisons across pools with no encounters.
    Ordered configured criteria otherwise apply; count/difference metrics are
    per match under points_per_match. Quotients retain their declared behavior.
    """
    groups=defaultdict(list)
    criteria=[c for c in rules['tie_breaks'] if c not in ('head_to_head','manual','playoff')]
    def number(value):return float('inf') if value=='∞' else value
    for row in rows:
        metrics=[]
        for criterion in criteria:
            value=number(row[criterion])
            if rules['cross_pool']=='points_per_match' and criterion in ('standings_points','won','set_difference','point_difference'):
                value=value/row['played'] if row['played'] else 0
            metrics.append(value)
        groups[tuple(metrics)].append(row)
    ordered=[];rank=1
    for metrics in sorted(groups,reverse=True):
        group=groups[metrics]
        manual=bool('manual' in rules['tie_breaks'] and resolution and resolution.get('reason') and all(row['entry_id'] in resolution.get('order',[]) for row in group))
        for offset,row in enumerate(sorted(group,key=lambda r:resolution['order'].index(r['entry_id']) if manual else r['seed'])):
            ordered.append({**row,'comparison_rank':rank+offset if manual else rank,'comparison_tied':len(group)>1 and not manual,
                'comparison_policy':rules['cross_pool'],'comparison_metrics':[('∞' if x==float('inf') else x) for x in metrics],
                'comparison_resolution_reason':resolution['reason'] if manual else None})
        rank+=len(group)
    return ordered
