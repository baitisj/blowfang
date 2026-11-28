"""kok npz -> TI-LPC rendered npz.  render_lpc.py IN.npz OUT.npz MODE (mix | 0281 | 5220) [procs]"""
import sys, numpy as np
sys.path.insert(0,'/tmp/claude-0/-home-user/4f48aaa3-4961-5224-a4f8-d802486df25e/scratchpad/sas')
from multiprocessing import Pool
def work(a):
    import lpcfast
    i,x,mode=a; rng=np.random.default_rng(424242+i)
    chip=mode if mode!='mix' else ('0281' if rng.random()<0.6 else '5220')
    y=lpcfast.render(x.astype(np.float64)/32768,chip,seed=i)
    y=y/(np.max(np.abs(y))+1e-9)*rng.uniform(0.3,0.9)
    return (np.clip(y*32767,-32767,32767)).astype(np.int16), chip
if __name__=='__main__':
    inp,outp,mode=sys.argv[1:4]; d=np.load(inp,allow_pickle=True)
    with Pool(int(sys.argv[4]) if len(sys.argv)>4 else 4) as p:
        R=p.map(work,[(i,x,mode) for i,x in enumerate(d['pcm'])],chunksize=16)
    pcm=np.empty(len(R),object); pcm[:]=[r[0] for r in R]
    np.savez(outp,pcm=pcm,lab=d['lab'],text=d['text'],voice=np.array([f'{v}|{r[1]}' for v,r in zip(d['voice'],R)]))
    print('wrote',outp,len(R))
