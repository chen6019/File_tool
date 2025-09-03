from __future__ import annotations
import os, re, shutil
from dataclasses import dataclass
from PIL import Image
from ..utils import next_non_conflict

def parse_custom_ratios(text:str)->list[tuple[int,int,str]]:
    text=text.strip()
    if not text:
        text='16:9,16:10,4:3,3:2,5:4,21:9,1:1'
    pairs=[]
    for token in re.split(r'[;,\s]+',text):
        if not token: continue
        token=token.lower().replace('x',':')
        if ':' not in token: continue
        a,b=token.split(':',1)
        if a.isdigit() and b.isdigit():
            w=int(a); h=int(b)
            if 0<w<=10000 and 0<h<=10000:
                pairs.append((w,h,f'{w}x{h}'))
    uniq={}
    for w,h,label in pairs: uniq[(w,h)]=label
    return [(w,h,lbl) for (w,h),lbl in uniq.items()]

def classify(file_list:list[str], base_out:str, preview:bool, tol:float, snap:bool, ratio_text:str, q_log):
    common=parse_custom_ratios(ratio_text)
    if not common: return file_list
    res=[]
    for p in file_list:
        if not os.path.isfile(p):
            res.append(p); continue
        try:
            with Image.open(p) as im:
                w,h=im.size
        except Exception:
            res.append(p); continue
        if h==0:
            res.append(p); continue
        ratio=w/h; label='other'; best=(None,1e9)
        for rw,rh,lbl in common:
            ideal=rw/rh
            if ideal==0: continue
            diff=abs(ratio-ideal)/ideal
            if diff<best[1]: best=(lbl,diff)
            if diff<=tol:
                label=lbl; break
        if label=='other' and snap and best[0]:
            label=best[0]
        target_dir=os.path.join(base_out,label)
        os.makedirs(target_dir,exist_ok=True)
        dest=os.path.join(target_dir, os.path.basename(p))
        if os.path.abspath(dest)==os.path.abspath(p):
            res.append(p); continue
        if os.path.exists(dest):
            dest=next_non_conflict(dest) if not preview else dest+'(预览冲突)'
        try:
            if preview:
                shutil.copy2(p,dest)
            else:
                shutil.move(p,dest)
            q_log('LOG\tRENAME\t'+p+'\t'+dest+'\t比例分类->'+label)
            res.append(dest)
        except Exception as e:
            q_log('LOG\tRENAME\t'+p+'\t'+p+'\t比例分类失败:'+str(e))
            res.append(p)
    return res
