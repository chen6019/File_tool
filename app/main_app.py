"""模块化 GUI 启动入口 (简化示例)
分阶段: 分类 -> 转换 -> 去重 -> 重命名
保留原日志格式, 仅演示架构拆分。
"""
import os, sys, threading, queue, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from .utils import iter_images
from .stages import classify, convert, rename, dedupe

class ModularApp:
    def __init__(self, root:tk.Tk):
        self.root=root; root.title('图片工具(模块化)')
        self.q=queue.Queue(); self.stop_flag=threading.Event(); self.worker=None
        self.files=[]; self.preview=False
        # Vars
        self.enable_classify=tk.BooleanVar(value=False)
        self.enable_convert=tk.BooleanVar(value=True)
        self.enable_dedupe=tk.BooleanVar(value=True)
        self.enable_rename=tk.BooleanVar(value=True)
        self.in_var=tk.StringVar(); self.out_var=tk.StringVar()
        self.classify_tol=tk.DoubleVar(value=0.03); self.classify_custom=tk.StringVar(value='16:9,4:3,1:1')
        self.classify_snap=tk.BooleanVar(value=False)
        self.pattern_var=tk.StringVar(value='{name}_{index}.{fmt}')
        self.start_var=tk.IntVar(value=1); self.step_var=tk.IntVar(value=1); self.width_var=tk.IntVar(value=0)
        self.overwrite_var=tk.StringVar(value='覆盖原有')
        self.keep_mode_var=tk.StringVar(value='最大分辨率'); self.action_var=tk.StringVar(value='仅列出')
        self.th_var=tk.IntVar(value=0)
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
        # 简化: 仅分类与重命名参数示例
        clsf=ttk.LabelFrame(outer,text='分类参数'); clsf.pack(fill='x',pady=4)
        ttk.Label(clsf,text='容差').grid(row=0,column=0,sticky='e')
        ttk.Entry(clsf,textvariable=self.classify_tol,width=6).grid(row=0,column=1)
        ttk.Checkbutton(clsf,text='吸附最近',variable=self.classify_snap).grid(row=0,column=2,padx=4)
        ttk.Label(clsf,text='自定义').grid(row=0,column=3,sticky='e')
        ttk.Entry(clsf,textvariable=self.classify_custom,width=40).grid(row=0,column=4,sticky='we')
        clsf.columnconfigure(4,weight=1)
        rename_box=ttk.LabelFrame(outer,text='重命名'); rename_box.pack(fill='x',pady=4)
        ttk.Label(rename_box,text='模式').grid(row=0,column=0,sticky='e')
        ttk.Entry(rename_box,textvariable=self.pattern_var,width=42).grid(row=0,column=1,sticky='w')
        ttk.Label(rename_box,text='起始').grid(row=0,column=2,sticky='e'); ttk.Entry(rename_box,textvariable=self.start_var,width=6).grid(row=0,column=3)
        ttk.Label(rename_box,text='步长').grid(row=0,column=4,sticky='e'); ttk.Entry(rename_box,textvariable=self.step_var,width=4).grid(row=0,column=5)
        ttk.Label(rename_box,text='宽度').grid(row=0,column=6,sticky='e'); ttk.Entry(rename_box,textvariable=self.width_var,width=4).grid(row=0,column=7)
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
            # 省略：调用 convert.batch_convert 的完整参数，这里仅示意
            pass
        if self.enable_dedupe.get() and len(files)>1:
            pass  # 同理省略
        if self.enable_rename.get():
            pass
        self._log('STATUS 完成')

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
