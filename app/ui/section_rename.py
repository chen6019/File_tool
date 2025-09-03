import tkinter as tk
from tkinter import ttk
from ..config import OVERWRITE_OPTIONS

class RenameSection:
    def __init__(self,parent,vars_obj):
        self.vars=vars_obj
        frame=ttk.LabelFrame(parent,text='重命名')
        self.frame=frame
        self._build()

    def _build(self):
        v=self.vars; f=self.frame
        ttk.Label(f,text='模式').grid(row=0,column=0,sticky='e')
        ttk.Entry(f,textvariable=v['pattern'],width=42).grid(row=0,column=1,sticky='w')
        ttk.Label(f,text='起始').grid(row=0,column=2,sticky='e'); ttk.Entry(f,textvariable=v['start'],width=6).grid(row=0,column=3)
        ttk.Label(f,text='步长').grid(row=0,column=4,sticky='e'); ttk.Entry(f,textvariable=v['step'],width=4).grid(row=0,column=5)
        ttk.Label(f,text='宽度').grid(row=0,column=6,sticky='e'); ttk.Entry(f,textvariable=v['width'],width=4).grid(row=0,column=7)
        ttk.Label(f,text='覆盖').grid(row=0,column=8,sticky='e')
        ttk.Combobox(f,textvariable=v['overwrite'],values=OVERWRITE_OPTIONS,width=10,state='readonly').grid(row=0,column=9,sticky='w')

    def widget(self): return self.frame
