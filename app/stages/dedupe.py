import os, threading, queue, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from PIL import Image
from ..utils import norm_ext, next_non_conflict, safe_delete

@dataclass
class ImgInfo:
    path:str; size:int; w:int; h:int; ah:int; dh:int; mtime:float
    @property
    def res(self): return self.w*self.h

def ahash(im):
    im=im.convert('L').resize((8,8))
    avg=sum(im.getdata())/64.0
    bits=0
    for i,p in enumerate(im.getdata()):
        if p>=avg: bits|=1<<i
    return bits

def dhash(im):
    im=im.convert('L').resize((9,8))
    pixels=list(im.getdata())
    bits=0; idx=0
    for r in range(8):
        row=pixels[r*9:(r+1)*9]
        for c in range(8):
            if row[c] > row[c+1]: bits|=1<<idx
            idx+=1
    return bits

def hamming(a:int,b:int)->int:
    return (a^b).bit_count()

def dedupe(files, keep_mode, action, move_dir, th, preview, workers, q_log, simulate_delete):
    infos=[]; lock=threading.Lock(); done=0
    def compute(path):
        nonlocal done
        try:
            with Image.open(path) as im:
                w,h=im.size; a=ahash(im); d=dhash(im); st=os.stat(path)
            info=ImgInfo(path,st.st_size,w,h,a,d,st.st_mtime)
        except Exception:
            info=None
        with lock:
            done+=1
        return info
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(compute,f) for f in files]):
            r=fut.result();
            if r: infos.append(r)
    groups=[]
    for info in infos:
        placed=False
        for g in groups:
            rep=g[0]
            if th==0:
                if info.ah==rep.ah and info.dh==rep.dh: g.append(info); placed=True; break
            else:
                if hamming(info.ah,rep.ah)+hamming(info.dh,rep.dh)<=th: g.append(info); placed=True; break
        if not placed: groups.append([info])
    dup=[g for g in groups if len(g)>1]
    kept=[]
    for gi,g in enumerate(sorted(dup,key=lambda x:-len(x)),1):
        if keep_mode=='largest': keep=max(g,key=lambda x:x.res)
        elif keep_mode=='largest-file': keep=max(g,key=lambda x:x.size)
        elif keep_mode=='newest': keep=max(g,key=lambda x:x.mtime)
        elif keep_mode=='oldest': keep=min(g,key=lambda x:x.mtime)
        else: keep=g[0]
        kept.append(keep.path)
        for o in (x for x in g if x is not keep):
            act='保留'
            if action=='delete':
                if preview:
                    simulate_delete(o.path); act='删除(预览)'
                else:
                    ok,msg=safe_delete(o.path); act=msg
            elif action=='move' and move_dir:
                if preview:
                    act='移动(预览)'
                else:
                    try:
                        os.makedirs(move_dir,exist_ok=True)
                        target=os.path.join(move_dir,os.path.basename(o.path))
                        if os.path.exists(target): target=next_non_conflict(target)
                        shutil.move(o.path,target); act='移动'
                    except Exception as e: act=f'移失败:{e}'
            q_log(f'LOG\tDEDUP\t{o.path}\t{keep.path}\t{act}')
        q_log(f'LOG\tDEDUP\t{keep.path}\t组#{gi}\t保留({len(g)})')
    dup_paths={x.path for grp in dup for x in grp}
    for p in files:
        if p not in dup_paths: kept.append(p)
    return kept
