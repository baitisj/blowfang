"""Spotter vs Jeff's labels: eval_real.py MODEL.pkl [more models]"""
import sys,glob,json,pickle,numpy as np,soundfile as sf
sys.path.insert(0,'.')
import jax,jax.numpy as jnp,train as T,textgen as tg,feats as F
F.BIN='/home/user/kws/kws_feat'
DS=['/mnt/project-files/k6-kws/labels/db-20261123T0108Z','/mnt/project-files/k6-kws/labels/db-20261123T0538Z','/mnt/project-files/k6-kws/labels/db-20261123T0757Z','/mnt/project-files/k6-kws/labels/db-20261123T1535Z','/mnt/project-files/k6-kws/labels/db-20261124T0330Z']
NORM={'x-ray':'x-ray','xray':'x-ray','juliet':'juliett'}
import os
if os.environ.get('KWS_LETTERS'):
    NORM.update({c:'letter-'+c for c in 'abcdefghijklmnopqrstuvwxyz'}); NORM.update({str(i):w for i,w in enumerate(['zero','one','two','three','four','five','six','seven','eight','nine'])})
V=tg.VOCAB; IDX={w:i for i,w in enumerate(V)}
def segs():
    for D in DS:
     for f in sorted(glob.glob(D+'/labels/*.json')):
        d=json.load(open(f)); d=d.get('data',d); cid=d['clip']
        x,_=sf.read(f'{D}/audio/{cid}.wav')
        ws=[(NORM.get(w['w'],w['w']),w['t0'],w['t1']) for w in d['words']]
        ws=[w for w in ws if w[0] in IDX]
        # group contiguous vocab words (gap < 1.0 s) into segments
        g=[];cur=[]
        for w in ws:
            if cur and w[1]-cur[-1][2]>1.0: g.append(cur); cur=[]
            cur.append(w)
        if cur: g.append(cur)
        for k,s in enumerate(g):
            a=max(0,int((s[0][1]-0.35)*8000)); b=min(len(x),int((s[-1][2]+0.35)*8000))
            yield f'{cid}#{k}',x[a:b],[IDX[w[0]] for w in s]
S=list(segs())
for mp in sys.argv[1:]:
    m=pickle.load(open(mp,'rb')); p=jax.tree_util.tree_map(jnp.asarray,m['params']); st=m['args']['stride']; T.CMN=bool(m['args'].get('cmn',0)); T.NOPITCH=bool(m['args'].get('nopitch',0))
    fw=jax.jit(lambda p,x:T.fwd(p,x,st)); E=N=0
    print('==',mp.split('/')[-1])
    for name,x,lab in S:
        x=(x/(np.max(np.abs(x))+1e-9)*0.5).astype(np.float32)
        f=T.cmn(F.norm_sotto(F.sotto(x)),0); h=T.greedy(np.asarray(fw(p,jnp.asarray(f[None])))[0]); h=[i for i in h if i<len(V)]
        e=T.edit(h,lab); E+=e; N+=len(lab)
        print(f'  {name:16s} ref {" ".join(V[i] for i in lab):45s} hyp {" ".join(V[i] for i in h)}')
    print(f'  WER-ish {E}/{N} = {100*E/N:.0f}%')
