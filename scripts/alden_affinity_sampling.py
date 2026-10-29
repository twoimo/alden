"""Bounded candidate traversal over already admitted, ID-sorted vectors.

This module proposes comparisons only. Cosine scoring, source authority, output
allocation and resource/cancellation guards remain with the existing evaluator.
"""
from dataclasses import dataclass
import hashlib

TOURNAMENT='project-round-robin-v1'
PARTITION='sparse-partition-tournament-v1'
TREES=4
LEAF_SIZE=16
WIDTH=16

def cover_pairs(groups,n,guard):
    """At most N permitted pairs cover every node in a non-singleton group."""
    covered=bytearray(n);cover=[]
    for members in groups:
        guard.check();uncovered=[i for i in members if not covered[i]]
        for at in range(0,len(uncovered)-1,2):
            i,j=uncovered[at:at+2];cover.append((min(i,j),max(i,j)));covered[i]=covered[j]=1
        if len(uncovered)%2:
            i=uncovered[-1];j=members[0]if members[0]!=i else members[1]
            cover.append((min(i,j),max(i,j)));covered[i]=covered[j]=1
    return cover

@dataclass
class PairPlan:
    method:str
    cap:int
    nodes:int
    projects:list
    partitions:list
    cover:list
    projection_products:int=0

    def pairs(self,tournament,guard):
        if self.method==TOURNAMENT:
            yield from tournament(self.projects,self.nodes,self.cap,guard)
            return
        seen=bytearray((self.nodes*(self.nodes-1)//2+7)//8);yielded=0
        families=(iter(self.cover),tournament(self.partitions,self.nodes,self.cap,guard),
                  tournament(self.projects,self.nodes,self.cap,guard))
        for family in families:
            for i,j in family:
                if yielded%64==0:guard.check()
                i,j=min(i,j),max(i,j);key=j*(j-1)//2+i
                byte,bit=key>>3,1<<(key&7)
                if seen[byte]&bit:continue
                seen[byte]|=bit;yielded+=1;yield i,j
                if yielded>=self.cap:return

def build_plan(loaded,groups,eligible,budgets,guard,*,dimension):
    """Projection work is reserved before reducing the dot-product allowance.

    A source-permitted cover has at most N pairs: every eligible node is paired
    once, except already-covered partners of odd groups. Global partitions keep
    projection work independent of the number of overlapping project labels;
    project intersection is reapplied before any proposed pair is scored.
    """
    n=len(loaded);cap=min(budgets.max_pairs,budgets.max_products//dimension)
    old=lambda:PairPlan(TOURNAMENT,cap,n,groups,[],[])
    depth=max(0,(n-1).bit_length()-4)
    estimated=TREES*n*depth*WIDTH
    remaining=(budgets.max_products-estimated)//dimension
    if (n<128 or eligible<=cap or dimension<WIDTH
            or estimated>budgets.max_products//8 or min(cap,remaining)<max(4096,4*n)):
        return old()
    leaves=[];products=0
    def split(members,tree,path):
        nonlocal products
        guard.check()
        if len(members)<=LEAF_SIZE:leaves.append(members);return
        coordinates=sorted(range(dimension),key=lambda d:hashlib.sha256(
            f'tree:{tree}:{path}:{d}'.encode()).digest())[:WIDTH]
        signs=[1 if hashlib.sha256(f'side:{tree}:{path}:{d}'.encode()).digest()[0]%2 else -1
               for d in coordinates]
        values=[]
        for at,index in enumerate(members):
            if at%64==0:guard.check()
            vector=loaded[index][1]
            value=sum(vector[d]*sign for d,sign in zip(coordinates,signs))
            values.append((value,loaded[index][0]['id'],index))
        products+=len(members)*WIDTH;values.sort()
        ordered=[index for _,_,index in values];middle=len(ordered)//2
        split(ordered[:middle],tree,path+'0');split(ordered[middle:],tree,path+'1')
    for tree in range(TREES):split(list(range(n)),tree,'0:')
    if products>estimated:raise RuntimeError('affinity_projection_budget')
    partitions=[]
    for at,leaf in enumerate(leaves):
        if at%32==0:guard.check()
        projects={}
        for index in leaf:
            for project in sorted(set(loaded[index][0]['projects'])):
                projects.setdefault(project,[]).append(index)
        partitions.extend(sorted(members)for _,members in sorted(projects.items())if len(members)>1)
    cover=cover_pairs(groups,n,guard)
    guard.check()
    return PairPlan(PARTITION,min(cap,(budgets.max_products-products)//dimension),n,
                    groups,partitions,cover,products)
