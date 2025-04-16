import argparse
import sys
from tkinter import *
from tkinter import ttk, filedialog
from PIL import Image, ImageTk
import os

def convert_image(input_path, output_path, format):
    try:
        with Image.open(input_path) as img:
            if format.lower() == 'jpg':
                save_format = 'JPEG'
                extension = 'jpg'
            else:
                save_format = format.upper()
                extension = format.lower()

            if output_path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                if not output_path.lower().endswith(f'.{extension}'):
                    output_path = os.path.splitext(output_path)[0] + f'.{extension}'
                img.save(output_path, format=save_format)
            else:
                output_file = os.path.join(output_path, os.path.splitext(os.path.basename(input_path))[0] + f'.{extension}')
                img.save(output_file, format=save_format)
            return f"成功转换: {input_path} -> {output_path}"
    except Exception as e:
        print(f"转换失败: {input_path} - {str(e)}")

class ImageConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title('图片格式转换器')
        
        # 创建主框架
        mainframe = ttk.Frame(root, padding="30 20 30 20")
        mainframe.grid(column=0, row=0, sticky=(N, W, E, S), padx=20, pady=20)

        # 创建样式对象
        style = ttk.Style()
        style.configure('TButton', padding=6)
        style.configure('TLabel', padding=5)
        style.configure('TEntry', padding=5)

        # 输入文件选择
        ttk.Label(mainframe, text="输入文件:").grid(column=0, row=0, sticky=W, padx=5, pady=8)
        self.input_entry = ttk.Entry(mainframe, width=40)
        self.input_entry.grid(column=1, row=0, sticky=(W, E), padx=5, pady=8)
        ttk.Button(mainframe, text="浏览", command=self.select_input).grid(column=2, row=0, sticky=W, padx=5, pady=8)

        # 输出格式选择
        ttk.Label(mainframe, text="目标格式:").grid(column=0, row=1, sticky=W, padx=5, pady=8)
        self.format_combo = ttk.Combobox(mainframe, values=['jpg', 'png', 'webp'], state='readonly')
        self.format_combo.grid(column=1, row=1, sticky=W, padx=5, pady=8)

        ttk.Label(mainframe, text="陈建金版权所有",foreground='red',).grid(column=2, row=1, sticky=W, padx=5, pady=8)

        # 输出路径选择
        ttk.Label(mainframe, text="输出路径:").grid(column=0, row=2, sticky=W, padx=5, pady=8)
        self.output_entry = ttk.Entry(mainframe, width=40)
        self.output_entry.grid(column=1, row=2, sticky=(W, E), padx=5, pady=8)
        ttk.Button(mainframe, text="浏览", command=self.select_output).grid(column=2, row=2, sticky=W, padx=5, pady=8)

        # 图片预览区域
        self.preview_label = ttk.Label(mainframe)
        self.preview_label.grid(column=0, row=3, columnspan=3, pady=15)

        # 转换按钮
        ttk.Button(mainframe, text="开始转换", command=self.start_conversion).grid(column=1, row=4, pady=15, ipadx=10, ipady=5)

        # 状态提示
        self.status_label = ttk.Label(mainframe, text="")
        self.status_label.grid(column=0, row=5, columnspan=3)

        # 绑定输入文件变化事件
        self.input_entry.bind('<KeyRelease>', self.update_preview)

        # 绑定格式选择变化事件
        self.format_combo.bind('<<ComboboxSelected>>', self.update_output_path)
        self.last_auto_path = ""

    def select_input(self):
        file_path = filedialog.askopenfilename(filetypes=[("图片文件", ".jpg .jpeg .png .webp")])
        if file_path:
            self.input_entry.delete(0, END)
            self.input_entry.insert(0, file_path)
            self.update_preview()

    def select_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=f".{self.format_combo.get()}",
            filetypes=[("图片文件", ".jpg .jpeg .png .webp")]
        )
        if path:
            self.output_entry.delete(0, END)
            self.output_entry.insert(0, path)
            self.last_auto_path = path

    def update_output_path(self, event=None):
        input_path = self.input_entry.get()
        if os.path.isfile(input_path):
            base = os.path.splitext(os.path.basename(input_path))[0]
            output_dir = os.path.dirname(input_path)
            fmt = self.format_combo.get()
            new_path = os.path.join(output_dir, f"{base}.{fmt}")
            
            # 保留用户手动修改的路径
            if self.output_entry.get() == self.last_auto_path:
                self.output_entry.delete(0, END)
                self.output_entry.insert(0, new_path)
                self.last_auto_path = new_path

    def update_preview(self, event=None):
        input_path = self.input_entry.get()
        if os.path.isfile(input_path):
            try:
                # 生成默认输出路径
                base = os.path.splitext(os.path.basename(input_path))[0]
                output_dir = os.path.dirname(input_path)
                fmt = self.format_combo.get() or 'jpg'
                default_path = os.path.join(output_dir, f"{base}.{fmt}")
                
                if not self.output_entry.get() or self.output_entry.get() == self.last_auto_path:
                    self.output_entry.delete(0, END)
                    self.output_entry.insert(0, default_path)
                    self.last_auto_path = default_path
                
                # 原有预览逻辑
                img = Image.open(input_path)
                max_size = 400
                width, height = img.size
                ratio = min(max_size/width, max_size/height)
                new_size = (int(width*ratio), int(height*ratio))
                
                img = img.resize(new_size, Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.preview_label.configure(image=photo)
                self.preview_label.image = photo
                
                # 触发路径更新
                self.update_output_path()
            
            except Exception as e:
                self.status_label.config(text=f"预览失败: {str(e)}", foreground='red')
        else:
            self.last_auto_path = ""

    def select_input(self):
        file_path = filedialog.askopenfilename(filetypes=[("图片文件", ".jpg .jpeg .png .webp")])
        if file_path:
            self.input_entry.delete(0, END)
            self.input_entry.insert(0, file_path)
            self.update_preview()

    def select_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=f".{self.format_combo.get()}",
            filetypes=[("图片文件", ".jpg .jpeg .png .webp")]
        )
        if path:
            self.output_entry.delete(0, END)
            self.output_entry.insert(0, path)
            self.last_auto_path = path

    def start_conversion(self):
        input_path = self.input_entry.get()
        output_path = self.output_entry.get()
        format = self.format_combo.get()

        if not input_path or not output_path:
            self.status_label.config(text="请填写所有必填项", foreground='red')
            return

        try:
            result = convert_image(input_path, output_path, format)
            self.status_label.config(text=result, foreground='green')
        except Exception as e:
            self.status_label.config(text=f"转换失败: {str(e)}", foreground='red')

def main():
    # 判断是否使用命令行模式
    if len(sys.argv) > 1:
        # 原有命令行逻辑
        parser = argparse.ArgumentParser(description='图片格式转换工具')
        parser.add_argument('-i', '--input', required=True, help='输入文件或目录路径')
        parser.add_argument('-o', '--output', required=True, help='输出文件或目录路径')
        parser.add_argument('-f', '--format', required=True, choices=['jpg', 'png', 'webp'], help='目标格式 (jpg/png/webp)')
        
        args = parser.parse_args()
        
        if not os.path.exists(args.input):
            print("错误：输入路径不存在")
            return
        
        if os.path.isfile(args.input):
            convert_image(args.input, args.output, args.format)
        else:
            for filename in os.listdir(args.input):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    input_file = os.path.join(args.input, filename)
                    convert_image(input_file, args.output, args.format)
    else:
        # 启动GUI界面
        root = Tk()
        app = ImageConverterApp(root)
        root.mainloop()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n操作已取消")