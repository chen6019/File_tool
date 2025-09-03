import tkinter as tk
from tkinter import ttk

class LogView:
    def __init__(self, parent):
        frame=ttk.Frame(parent)
        self.frame=frame
        cols=[('stage','阶段',70),('src','源',260),('dst','目标',260),('info','信息',200)]
        self.tree=ttk.Treeview(frame,columns=[c[0] for c in cols],show='headings',height=14)
        for cid,txt,w in cols:
            self.tree.heading(cid,text=txt)
            self.tree.column(cid,width=w,anchor='w',stretch=True)
        vsb=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview)
        hsb=ttk.Scrollbar(frame,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set,xscrollcommand=hsb.set)
        self.tree.grid(row=0,column=0,sticky='nsew')
        vsb.grid(row=0,column=1,sticky='ns')
        hsb.grid(row=1,column=0,sticky='we')
        frame.columnconfigure(0,weight=1)
        frame.rowconfigure(0,weight=1)
        # tag colors
        self.tree.tag_configure('STAGE_DEDUPE', background='#FFF5E6')
        self.tree.tag_configure('STAGE_CONVERT', background='#E6F5FF')
        self.tree.tag_configure('STAGE_RENAME', background='#F0E6FF')
        self.tree.tag_configure('STAGE_DELETE', background='#FFE6E6')
        self.tree.tag_configure('STAGE_MOVE', background='#E6FFE6')
        self.tree.tag_configure('STAGE_KEEP', background='#F5F5F5')
        self.tree.tag_configure('STAGE_INFO', background='#EEEEEE')

    def widget(self):
        return self.frame

    def add_raw(self, raw_line:str):
        # raw format: LOG\tTYPE\tsrc\tdst\tinfo  or STATUS ...
        if raw_line.startswith('LOG\t'):
            _p=raw_line.split('\t',4)
            if len(_p)<5: return
            _,stage,src,dst,info=_p
            tag=self._stage_to_tag(stage)
            self.tree.insert('', 'end', values=(stage,src,dst,info), tags=(tag,src))
        elif raw_line.startswith('STATUS'):
            self.tree.insert('', 'end', values=('INFO','', '', raw_line), tags=('STAGE_INFO',))
        self.tree.yview_moveto(1)

    @staticmethod
    def _stage_to_tag(stage:str)->str:
        m=stage.upper()
        if m=='DEDUP': return 'STAGE_DEDUPE'
        if m=='CONVERT': return 'STAGE_CONVERT'
        if m=='RENAME': return 'STAGE_RENAME'
        if m=='DELETE': return 'STAGE_DELETE'
        if m=='MOVE': return 'STAGE_MOVE'
        if m=='KEEP': return 'STAGE_KEEP'
        return 'STAGE_INFO'
