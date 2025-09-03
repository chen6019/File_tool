import tkinter as tk
from tkinter import ttk

class ConvertSection:
    def __init__(self,parent,vars_obj):
        self.vars=vars_obj
        frame=ttk.LabelFrame(parent,text='转换参数')
        self.frame=frame
        self._build()

    def _build(self):
        v=self.vars; f=self.frame
        ttk.Label(f,text='格式').grid(row=0,column=0,sticky='e')
        ttk.Combobox(f,textvariable=v['fmt'],values=['jpg','png','webp','ico','gif'],width=8,state='readonly').grid(row=0,column=1,sticky='w',padx=(0,6))
        ttk.Label(f,text='质量').grid(row=0,column=2,sticky='e')
        ttk.Spinbox(f,from_=1,to=100,textvariable=v['quality'],width=5).grid(row=0,column=3,sticky='w')
        ttk.Checkbutton(f,text='同格式也重存',variable=v['same']).grid(row=0,column=4,sticky='w',padx=4)
        ttk.Checkbutton(f,text='PNG压缩',variable=v['png3']).grid(row=0,column=5,sticky='w')
        ttk.Checkbutton(f,text='删源',variable=v['rm_src']).grid(row=0,column=6,sticky='w',padx=4)
        ttk.Label(f,text='ICO尺寸').grid(row=1,column=0,sticky='e',pady=(4,0))
        ttk.Entry(f,textvariable=v['ico_sizes'],width=24).grid(row=1,column=1,sticky='w',pady=(4,0))
        ttk.Checkbutton(f,text='保留原尺寸',variable=v['ico_keep']).grid(row=1,column=2,sticky='w',pady=(4,0))
        ttk.Label(f,text='非方:').grid(row=1,column=3,sticky='e')
        for i,(txt,val) in enumerate([('保持','keep'),('中心','center'),('左上','topleft'),('填充','fit')]):
            ttk.Radiobutton(f,text=txt,variable=v['ico_square'],value=val).grid(row=1,column=4+i,sticky='w')

    def widget(self): return self.frame
