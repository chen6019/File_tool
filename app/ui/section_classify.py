import tkinter as tk
from tkinter import ttk
from ..config import RATIO_PRESETS, DEFAULT_RATIOS

class ClassifySection:
    def __init__(self, parent, vars_obj):
        self.vars = vars_obj
        frame = ttk.LabelFrame(parent,text='分类参数')
        self.frame=frame
        self._build()

    def _build(self):
        v=self.vars
        frame=self.frame
        frame.columnconfigure(4,weight=1)
        ttk.Label(frame,text='容差').grid(row=0,column=0,sticky='e')
        ttk.Entry(frame,textvariable=v['tol'],width=6).grid(row=0,column=1)
        ttk.Checkbutton(frame,text='吸附最近',variable=v['snap']).grid(row=0,column=2,padx=4)
        ttk.Label(frame,text='自定义').grid(row=0,column=3,sticky='e')
        ttk.Entry(frame,textvariable=v['custom'],width=40).grid(row=0,column=4,sticky='we')
        # 预设
        bar=ttk.Frame(frame); bar.grid(row=1,column=0,columnspan=5,sticky='w',pady=(2,0))
        def toggle(val:str):
            cur=v['custom'].get().replace('；',';').replace('，',',').replace(';',',')
            parts=[p.strip() for p in cur.split(',') if p.strip()]
            low={p.lower():p for p in parts}; k=val.lower()
            if k in low: parts=[p for p in parts if p.lower()!=k]
            else: parts.append(val)
            v['custom'].set(','.join(parts))
        for r in RATIO_PRESETS:
            ttk.Button(bar,text=r,width=6,command=lambda x=r: toggle(x)).pack(side='left',padx=1)
        ttk.Button(bar,text='清空',width=6,command=lambda: v['custom'].set('')).pack(side='left',padx=(8,0))

    def widget(self): return self.frame
