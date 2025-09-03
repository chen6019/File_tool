import tkinter as tk
from tkinter import ttk
from ..config import KEEP_MODE_OPTIONS, ACTION_OPTIONS

class DedupeSection:
    def __init__(self,parent,vars_obj):
        self.vars=vars_obj
        frame=ttk.LabelFrame(parent,text='去重参数')
        self.frame=frame
        self._build()

    def _build(self):
        v=self.vars; f=self.frame
        ttk.Label(f,text='阈值').grid(row=0,column=0,sticky='e')
        ttk.Spinbox(f,from_=0,to=32,textvariable=v['th'],width=5).grid(row=0,column=1,sticky='w')
        ttk.Label(f,text='保留').grid(row=0,column=2,sticky='e')
        ttk.Combobox(f,textvariable=v['keep'],values=KEEP_MODE_OPTIONS,width=10,state='readonly').grid(row=0,column=3,sticky='w',padx=(0,6))
        ttk.Label(f,text='动作').grid(row=0,column=4,sticky='e')
        ttk.Combobox(f,textvariable=v['action'],values=ACTION_OPTIONS,width=10,state='readonly').grid(row=0,column=5,sticky='w')
        ttk.Label(f,text='移动到').grid(row=0,column=6,sticky='e')
        ttk.Entry(f,textvariable=v['move_dir'],width=24).grid(row=0,column=7,sticky='w')
        ttk.Button(f,text='选',command=v['pick_move_cb'],width=4).grid(row=0,column=8,sticky='w',padx=(4,0))

    def widget(self): return self.frame
