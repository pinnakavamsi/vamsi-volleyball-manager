"""Fixture generators return reviewable participant/dependency descriptors."""
from math import ceil,log2
from ..common import APIError

FORMATS={'round_robin','pools_knockout','single_elimination','league'}
def round_robin(entries,repeat=1):
    if not 1<=repeat<=10:raise APIError('Repeated encounters must be between 1 and 10.')
    slots=list(entries)
    if len(slots)%2:slots.append(None)
    result=[]
    for encounter in range(repeat):
        rotation=slots[:]
        for round_index in range(len(rotation)-1):
            for i in range(len(rotation)//2):
                a,b=rotation[i],rotation[-i-1]
                if a and b:result.append({'a':a,'b':b,'round':encounter*(len(rotation)-1)+round_index+1})
            rotation=[rotation[0],rotation[-1],*rotation[1:-1]]
    return result

def bracket(entries):
    count=2**ceil(log2(max(len(entries),2)))
    order=[1,2]
    while len(order)<count:
        total=len(order)*2+1;order=[v for seed in order for v in (seed,total-seed)]
    seeds={i+1:e for i,e in enumerate(entries)}
    descriptors=[];previous=[]
    for i in range(0,count,2):
        index=len(descriptors);descriptors.append({'a':seeds.get(order[i]),'b':seeds.get(order[i+1]),'round':1,'bye':order[i] not in seeds or order[i+1] not in seeds});previous.append(index)
    round_number=2
    while len(previous)>1:
        current=[]
        for i in range(0,len(previous),2):
            index=len(descriptors);descriptors.append({'a':None,'b':None,'source_a':previous[i],'source_b':previous[i+1],'round':round_number});current.append(index)
        previous=current;round_number+=1
    return descriptors
