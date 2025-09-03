import os, re, shutil
from ..utils import norm_ext, next_non_conflict

def batch_rename(files, pattern, start, step, pad_width, overwrite, remove_src, preview, out_dir, q_log, ratio_map=None):
    if not pattern:
        return
    idx=start
    for f in files:
        if not os.path.isfile(f):
            continue
        ext=norm_ext(f); stem=os.path.splitext(os.path.basename(f))[0]
        ratio_label=''
        if ratio_map and f in ratio_map:
            ratio_label=ratio_map.get(f,'')
        name_raw=pattern
        def repl_index(m):
            w=int(m.group(1)); return str(idx).zfill(w)
        name_raw=re.sub(r'\{index:(\d+)\}',repl_index,name_raw)
        if '{index}' in name_raw:
            name_raw=name_raw.replace('{index}', str(idx).zfill(pad_width) if pad_width>0 else str(idx))
        final=(name_raw
                .replace('{name}',stem)
                .replace('{ext}',f'.{ext}')
                .replace('{fmt}',ext)
                .replace('{ratio}',ratio_label))
        if '.' not in os.path.basename(final):
            final+=f'.{ext}'
        dest=os.path.join(out_dir,final)
        if os.path.abspath(dest)==os.path.abspath(f):
            idx+=step; continue
        if os.path.exists(dest):
            if overwrite=='skip':
                q_log(f'LOG\tRENAME\t{f}\t{dest}\t跳过(存在)'); idx+=step; continue
            elif overwrite=='rename' and not preview:
                dest=next_non_conflict(dest)
            elif overwrite=='rename' and preview:
                dest=dest+'(预览改名)'
        try:
            if preview:
                shutil.copy2(f,dest)
            else:
                if remove_src:
                    shutil.move(f,dest)
                else:
                    shutil.copy2(f,dest)
            q_log(f'LOG\tRENAME\t{f}\t{dest}\t重命名')
        except Exception as e:
            q_log(f'LOG\tRENAME\t{f}\t{dest}\t失败:{e}')
        idx+=step
