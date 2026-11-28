"""Known-ID template match on CMN'd sotto features. Template = one copy's callsign span; score all overs."""
import glob,subprocess,numpy as np,sys
sys.path.insert(0,'/home/user/kws/src')
import feats as F, train as T
from scipy.signal import butter,sosfiltfilt
F.BIN='/home/user/kws/kws_feat'; T.CMN=True
hp=butter(8,300,'hp',fs=8000,output='sos')
def feat(f):
    x=np.frombuffer(subprocess.run(['ffmpeg','-loglevel','error','-i',f,'-ac','1','-ar','8000','-f','f32le','-'],capture_output=True).stdout,np.float32).astype(float)
    x=sosfiltfilt(hp,x); x=(x/(np.max(np.abs(x))+1e-9)*0.5).astype(np.float32)
    return T.cmn(F.norm_sotto(F.sotto(x)),0)[:,2:]   # energy + shape (pitch/voicing dropped)
def score(Fx,Tm):
    n=len(Tm)
    if len(Fx)<n: Fx=np.vstack([Fx,np.zeros((n-len(Fx),Fx.shape[1]))])
    Tz=(Tm-Tm.mean(0))/(Tm.std(0)+1e-6); best=-1
    for i in range(len(Fx)-n+1):
        P=Fx[i:i+n]; Pz=(P-P.mean(0))/(P.std(0)+1e-6); s=(P*0+Pz*Tz).mean()
        if s>best: best=s; bi=i
    return best,bi*0.02
fs=sorted(glob.glob('/home/user/kws/cont/*/files/b2m/*.flac'))+sorted(glob.glob('/home/user/kws/n70/*/files/c70/*.flac'))
fs=[f for f in fs if not f.split('/')[-1].startswith(('440.1000','442.3000'))]
TM={'146.800 VE7RMR':('/home/user/kws/cont/kws-cont-c-r1/files/b2m/146.8000_20261122T1858545Z.flac',1.2,3.6),
    '442.250 W7ECG':('/home/user/kws/n70/kws-n70/files/c70/442.2500_20261123T0754008Z.flac',6.9,10.3)}
Fs={f:feat(f) for f in fs}
for name,(tf,a,b) in TM.items():
    Tm=feat(tf)[int(a/0.02):int(b/0.02)]
    res=sorted([(score(Fs[f],Tm),f.split('/')[-1]) for f in fs if f!=tf],reverse=True)
    print('==',name,'template',tf.split('/')[-1],f'{a}-{b}s')
    for (s,t),n in res[:14]: print('  %.3f @%.1fs %s'%(s,t,n))
    print('  ... median of rest %.3f'%np.median([s for (s,t),n in res[14:]]))
