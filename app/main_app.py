"""模块化 GUI 启动入口 (简化示例)
分阶段: 分类 -> 转换 -> 去重 -> 重命名
保留原日志格式, 仅演示架构拆分。
"""
import os, sys, threading, queue, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from .config import RATIO_PRESETS, KEEP_MODE_OPTIONS, ACTION_OPTIONS, OVERWRITE_OPTIONS

# 兼容包方式与直接脚本运行
try:  # 包内相对导入
    from .utils import iter_images, norm_ext, next_non_conflict
    from .stages import classify, convert, rename, dedupe
except Exception:
    # 直接运行: python app/main_app.py
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(base_dir)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    try:
        from app.utils import iter_images, norm_ext, next_non_conflict  # type: ignore
        from app.stages import classify, convert, rename, dedupe  # type: ignore
    except Exception:
        # 最后退: 同级无包结构
        from utils import iter_images, norm_ext, next_non_conflict  # type: ignore
        from stages import classify, convert, rename, dedupe  # type: ignore

class ModularApp:
    def __init__(self, root:tk.Tk):
            self.root = root
            root.title('图片工具(模块化)')
            # 运行控制
            self.q = queue.Queue()
            self.stop_flag = threading.Event()
            self.worker = None
            self.files = []
            self.preview = False
            # 功能开关
            self.enable_classify = tk.BooleanVar(value=False)
            self.enable_convert = tk.BooleanVar(value=True)
            self.enable_dedupe  = tk.BooleanVar(value=True)
            self.enable_rename  = tk.BooleanVar(value=True)
            # IO
            self.in_var  = tk.StringVar()
            self.out_var = tk.StringVar()
            # 分类参数
            self.classify_tol    = tk.DoubleVar(value=0.03)
            self.classify_custom = tk.StringVar(value='16:9,4:3,1:1')
            self.classify_snap   = tk.BooleanVar(value=False)
            self.ratio_presets = ['16:9','16:10','4:3','3:2','5:4','21:9','1:1']
            # 转换参数
            self.fmt_var            = tk.StringVar(value='webp')
            self.quality_var        = tk.IntVar(value=90)
            self.process_same       = tk.BooleanVar(value=False)
            self.png3               = tk.BooleanVar(value=False)
            self.ico_sizes_var      = tk.StringVar(value='')
            self.ico_keep_orig      = tk.BooleanVar(value=False)
            self.ico_square         = tk.StringVar(value='fit')
            self.remove_src_convert = tk.BooleanVar(value=False)
            # 重命名参数
            self.pattern_var  = tk.StringVar(value='{name}_{index}.{fmt}')
            self.start_var    = tk.IntVar(value=1)
            self.step_var     = tk.IntVar(value=1)
            self.width_var    = tk.IntVar(value=0)
            self.overwrite_var= tk.StringVar(value='覆盖原有')
            # 去重参数
            self.keep_mode_var = tk.StringVar(value='最大分辨率')
            self.action_var    = tk.StringVar(value='仅列出')
            self.th_var        = tk.IntVar(value=0)
            # 构建 UI
            self._build()
            self.root.after(200,self._drain)

    def _build(self):
        outer=ttk.Frame(self.root,padding=6); outer.pack(fill='both',expand=True)
        io=ttk.Frame(outer); io.pack(fill='x')
        ttk.Label(io,text='输入').grid(row=0,column=0,sticky='e')
        ttk.Entry(io,textvariable=self.in_var,width=40).grid(row=0,column=1,sticky='we')
        ttk.Button(io,text='目录',command=self._pick_dir,width=5).grid(row=0,column=2,padx=2)
        ttk.Button(io,text='文件',command=self._pick_file,width=5).grid(row=0,column=3,padx=2)
        ttk.Label(io,text='输出').grid(row=0,column=4,sticky='e')
        ttk.Entry(io,textvariable=self.out_var,width=32).grid(row=0,column=5,sticky='we')
        io.columnconfigure(1,weight=1); io.columnconfigure(5,weight=1)
        bar=ttk.Frame(outer); bar.pack(fill='x',pady=4)
        for txt,var in [('分类',self.enable_classify),('转换',self.enable_convert),('去重',self.enable_dedupe),('重命名',self.enable_rename)]:
            ttk.Checkbutton(bar,text=txt,variable=var).pack(side='left',padx=2)
        ttk.Button(bar,text='预览',command=lambda: self._start(True)).pack(side='right',padx=4)
        ttk.Button(bar,text='开始',command=lambda: self._start(False)).pack(side='right',padx=4)
        # 分类参数(拆分)
        from .ui.section_classify import ClassifySection
        cls_vars={'tol':self.classify_tol,'snap':self.classify_snap,'custom':self.classify_custom}
        self.classify_section=ClassifySection(outer,cls_vars)
        self._frame_classify=self.classify_section.widget()
        self.classify_section.widget().pack(fill='x',pady=4)
        # 转换参数(拆分)
        from .ui.section_convert import ConvertSection
        cv_vars={'fmt':self.fmt_var,'quality':self.quality_var,'same':self.process_same,'png3':self.png3,'rm_src':self.remove_src_convert,'ico_sizes':self.ico_sizes_var,'ico_keep':self.ico_keep_orig,'ico_square':self.ico_square}
        self.convert_section=ConvertSection(outer,cv_vars)
        self.convert_section.widget().pack(fill='x',pady=4)
        # 去重参数(拆分)
        from .ui.section_dedupe import DedupeSection
        self.move_dir_var=tk.StringVar()
        dd_vars={'th':self.th_var,'keep':self.keep_mode_var,'action':self.action_var,'move_dir':self.move_dir_var,'pick_move_cb':self._pick_move_dir}
        self.dedupe_section=DedupeSection(outer,dd_vars)
        self.dedupe_section.widget().pack(fill='x',pady=4)
        # 重命名(拆分)
        from .ui.section_rename import RenameSection
        rn_vars={'pattern':self.pattern_var,'start':self.start_var,'step':self.step_var,'width':self.width_var,'overwrite':self.overwrite_var}
        self.rename_section=RenameSection(outer,rn_vars)
        self.rename_section.widget().pack(fill='x',pady=4)
        # 日志视图 (拆分组件)
        from .ui.log_view import LogView
        log_frame=ttk.Frame(outer); log_frame.pack(fill='both',expand=True,pady=(6,0))
        self.log_view=LogView(log_frame)
        self.log_view.widget().pack(fill='both',expand=True)
        # 状态刷新
        for v in (self.enable_classify,self.enable_convert,self.enable_dedupe,self.enable_rename):
            v.trace_add('write', lambda *a: self._update_states())
        self._update_states()

    def _pick_dir(self):
        d=filedialog.askdirectory();
        if d: self.in_var.set(d)
    def _pick_file(self):
        f=filedialog.askopenfilename();
        if f: self.in_var.set(f)

    def _start(self, preview:bool):
        if self.worker and self.worker.is_alive(): return
        self.preview=preview
        inp=self.in_var.get().strip();
        if not inp: return
        if os.path.isdir(inp):
            self.files=list(iter_images(inp,True))
        elif os.path.isfile(inp):
            self.files=[inp]
        else:
            return
        self.worker=threading.Thread(target=self._run,daemon=True); self.worker.start()

    def _log(self,msg):
        self.q.put(msg)

    def _run(self):
        files=self.files
        out_dir=self.out_var.get().strip() or (os.path.dirname(files[0]) if files else os.getcwd())
        os.makedirs(out_dir,exist_ok=True)
        ratio_map={}
        if self.enable_classify.get() and len(files)>1:
            before_map={f:None for f in files}
            files=classify.classify(files,out_dir,self.preview,self.classify_tol.get(),self.classify_snap.get(),self.classify_custom.get(),self._log)
            # 推断 ratio 标签（目录名）
            for new_path in files:
                base_dir=os.path.basename(os.path.dirname(new_path))
                ratio_map[new_path]=base_dir
        if self.enable_convert.get():
            # 解析 ico 尺寸
            ico_sizes=None
            if self.fmt_var.get()=='ico' and not self.ico_keep_orig.get():
                sizes=[]
                for token in self.ico_sizes_var.get().replace('；',';').replace(',', ' ').replace(';',' ').split():
                    if token.isdigit():
                        v=int(token)
                        if 1<=v<=1024: sizes.append(v)
                if sizes: ico_sizes=sorted(set(sizes))[:10]
            files=convert.batch_convert(files,out_dir,self.fmt_var.get(),self.process_same.get(),self.quality_var.get(),self.png3.get(),ico_sizes,self.ico_square.get(),self.preview,self.remove_src_convert.get(),self._log)
        if self.enable_dedupe.get() and len(files)>1:
            from .stages.dedupe import dedupe as dedupe_fn
            keep_map={'最大分辨率':'largest','最大文件':'largest-file','最新':'newest','最旧':'oldest','首个':'first'}
            action_map={'仅列出':'list','删除重复':'delete','移动重复':'move'}
            files=dedupe_fn(files,keep_map.get(self.keep_mode_var.get(),'largest'),action_map.get(self.action_var.get(),'list'),self.move_dir_var.get().strip(),self.th_var.get(),self.preview,4,self._log,lambda p: None)
        if self.enable_rename.get():
            from .stages.rename import batch_rename
            overwrite_map={'覆盖原有':'overwrite','跳过已存在':'skip','自动改名':'rename'}
            batch_rename(files,self.pattern_var.get(),self.start_var.get(),self.step_var.get(),self.width_var.get(),overwrite_map.get(self.overwrite_var.get(),'overwrite'),False,self.preview,out_dir,self._log,ratio_map=ratio_map)
        self._log('STATUS 完成')

    def _update_states(self):
        # 分类区灰显
        enabled=self.enable_classify.get()
        if hasattr(self,'_frame_classify'):
            targets=[]
            for ch in self._frame_classify.winfo_children():
                targets.append(ch)
            for w in targets:
                try:
                    cls=w.winfo_class()
                    if cls=='TLabel' or cls.startswith('T') or cls in ('Entry','Button','TButton','Checkbutton','TCheckbutton'):
                        w.configure(state='normal' if enabled else 'disabled')
                except Exception:
                    pass
        # 其他区简单处理（可扩展）
        # 暂不灰显去重/重命名等，保持可操作

    def _pick_move_dir(self):
        d=filedialog.askdirectory();
        if d: self.move_dir_var.set(d)

    def _drain(self):
        try:
            while True:
                m=self.q.get_nowait(); self.log_view.add_raw(m)
        except queue.Empty:
            pass
        self.root.after(120,self._drain)


def launch():
    root=tk.Tk(); ModularApp(root); root.mainloop()

if __name__=='__main__':
    launch()
