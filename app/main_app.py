"""模块化 GUI 启动入口 (简化示例)
分阶段: 分类 -> 转换 -> 去重 -> 重命名
保留原日志格式, 仅演示架构拆分。
"""
import os, sys, threading, queue, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from .utils import iter_images, norm_ext, next_non_conflict
from .stages import classify, convert, rename, dedupe

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
        # 分类参数
        clsf=ttk.LabelFrame(outer,text='分类参数'); clsf.pack(fill='x',pady=4)
        ttk.Label(clsf,text='容差').grid(row=0,column=0,sticky='e')
        ttk.Entry(clsf,textvariable=self.classify_tol,width=6).grid(row=0,column=1)
        ttk.Checkbutton(clsf,text='吸附最近',variable=self.classify_snap).grid(row=0,column=2,padx=4)
        ttk.Label(clsf,text='自定义').grid(row=0,column=3,sticky='e')
        ttk.Entry(clsf,textvariable=self.classify_custom,width=40).grid(row=0,column=4,sticky='we')
        clsf.columnconfigure(4,weight=1)
        # 转换参数
        cv=ttk.LabelFrame(outer,text='转换参数'); cv.pack(fill='x',pady=4)
        ttk.Label(cv,text='格式').grid(row=0,column=0,sticky='e')
        ttk.Combobox(cv,textvariable=self.fmt_var,values=['jpg','png','webp','ico','gif'],width=8,state='readonly').grid(row=0,column=1,sticky='w',padx=(0,6))
        ttk.Label(cv,text='质量').grid(row=0,column=2,sticky='e')
        ttk.Spinbox(cv,from_=1,to=100,textvariable=self.quality_var,width=5).grid(row=0,column=3,sticky='w')
        ttk.Checkbutton(cv,text='同格式也重存',variable=self.process_same).grid(row=0,column=4,sticky='w',padx=4)
        ttk.Checkbutton(cv,text='PNG压缩',variable=self.png3).grid(row=0,column=5,sticky='w')
        ttk.Checkbutton(cv,text='删源',variable=self.remove_src_convert).grid(row=0,column=6,sticky='w',padx=4)
        ttk.Label(cv,text='ICO尺寸').grid(row=1,column=0,sticky='e',pady=(4,0))
        ttk.Entry(cv,textvariable=self.ico_sizes_var,width=24).grid(row=1,column=1,sticky='w',pady=(4,0))
        ttk.Checkbutton(cv,text='保留原尺寸',variable=self.ico_keep_orig).grid(row=1,column=2,sticky='w',pady=(4,0))
        ttk.Label(cv,text='非方:').grid(row=1,column=3,sticky='e')
        for i,(txt,val) in enumerate([('保持','keep'),('中心','center'),('左上','topleft'),('填充','fit')]):
            ttk.Radiobutton(cv,text=txt,variable=self.ico_square,value=val).grid(row=1,column=4+i,sticky='w')
        # 去重参数
        dd=ttk.LabelFrame(outer,text='去重参数'); dd.pack(fill='x',pady=4)
        ttk.Label(dd,text='阈值').grid(row=0,column=0,sticky='e'); ttk.Spinbox(dd,from_=0,to=32,textvariable=self.th_var,width=5).grid(row=0,column=1,sticky='w')
        ttk.Label(dd,text='保留').grid(row=0,column=2,sticky='e')
        ttk.Combobox(dd,textvariable=self.keep_mode_var,values=['最大分辨率','最大文件','最新','最旧','首个'],width=10,state='readonly').grid(row=0,column=3,sticky='w',padx=(0,6))
        ttk.Label(dd,text='动作').grid(row=0,column=4,sticky='e')
        ttk.Combobox(dd,textvariable=self.action_var,values=['仅列出','删除重复','移动重复'],width=10,state='readonly').grid(row=0,column=5,sticky='w')
        ttk.Label(dd,text='移动到').grid(row=0,column=6,sticky='e')
        self.move_dir_var=tk.StringVar()
        ttk.Entry(dd,textvariable=self.move_dir_var,width=24).grid(row=0,column=7,sticky='w')
        ttk.Button(dd,text='选',command=self._pick_move_dir,width=4).grid(row=0,column=8,sticky='w',padx=(4,0))
        rename_box=ttk.LabelFrame(outer,text='重命名'); rename_box.pack(fill='x',pady=4)
        ttk.Label(rename_box,text='模式').grid(row=0,column=0,sticky='e')
        ttk.Entry(rename_box,textvariable=self.pattern_var,width=42).grid(row=0,column=1,sticky='w')
        ttk.Label(rename_box,text='起始').grid(row=0,column=2,sticky='e'); ttk.Entry(rename_box,textvariable=self.start_var,width=6).grid(row=0,column=3)
        ttk.Label(rename_box,text='步长').grid(row=0,column=4,sticky='e'); ttk.Entry(rename_box,textvariable=self.step_var,width=4).grid(row=0,column=5)
        ttk.Label(rename_box,text='宽度').grid(row=0,column=6,sticky='e'); ttk.Entry(rename_box,textvariable=self.width_var,width=4).grid(row=0,column=7)
        ttk.Label(rename_box,text='覆盖').grid(row=0,column=8,sticky='e')
        ttk.Combobox(rename_box,textvariable=self.overwrite_var,values=['覆盖原有','跳过已存在','自动改名'],width=10,state='readonly').grid(row=0,column=9,sticky='w')
        log_frame=ttk.Frame(outer); log_frame.pack(fill='both',expand=True,pady=(6,0))
        self.log=tk.Listbox(log_frame,height=16); self.log.pack(fill='both',expand=True)

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
        if self.enable_classify.get() and len(files)>1:
            files=classify.classify(files,out_dir,self.preview,self.classify_tol.get(),self.classify_snap.get(),self.classify_custom.get(),self._log)
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
            batch_rename(files,self.pattern_var.get(),self.start_var.get(),self.step_var.get(),self.width_var.get(),overwrite_map.get(self.overwrite_var.get(),'overwrite'),False,self.preview,out_dir,self._log)
        self._log('STATUS 完成')

    def _pick_move_dir(self):
        d=filedialog.askdirectory();
        if d: self.move_dir_var.set(d)

    def _drain(self):
        try:
            while True:
                m=self.q.get_nowait(); self.log.insert('end',m); self.log.yview_moveto(1)
        except queue.Empty:
            pass
        self.root.after(120,self._drain)


def launch():
    root=tk.Tk(); ModularApp(root); root.mainloop()

if __name__=='__main__':
    launch()
