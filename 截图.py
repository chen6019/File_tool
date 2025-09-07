import tkinter as tk
from tkinter import ttk, filedialog, simpledialog
from PIL import ImageGrab, ImageTk, ImageDraw, Image
import pyautogui

class ScreenshotApp:
	def __init__(self, root):
		self.root = root
		self.root.title("Windows截图工具")
		
		# 创建主界面
		self.frame = ttk.Frame(self.root, padding=10)
		self.frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S)) # type: ignore

		# 添加截图预览画布
		self.canvas = tk.Canvas(self.root, width=0, height=0)
		self.canvas.grid(row=1, column=0, padx=10, pady=10, sticky=(tk.W, tk.E, tk.N, tk.S)) # type: ignore

		# 功能按钮
		ttk.Button(self.frame, text="全屏截图", command=self.fullscreen_capture).grid(row=0, column=0, padx=5)
		ttk.Button(self.frame, text="区域截图", command=self.region_capture).grid(row=0, column=1, padx=5)
		ttk.Button(self.frame, text="退出", command=root.quit).grid(row=0, column=2, padx=5)
		
		ttk.Button(self.frame, text="保存", command=self.save_image).grid(row=0, column=3, padx=5)
		ttk.Button(self.frame, text="清除预览", command=self.clear_preview).grid(row=0, column=4, padx=5)

		# 状态提示
		# 状态提示样式
		# 版权声明
		# self.copyright_label = ttk.Label(self.root, text="陈建金版权所有", foreground='gray', font=('Arial', 8))
		# self.copyright_label.grid(row=2, column=0, pady=5, sticky=tk.S)

		# 状态提示
		self.status_label = ttk.Label(self.frame, text="准备就绪", foreground='green', font=('Arial', 10))
		self.status_label.grid(row=1, column=0, columnspan=5, padx=10, pady=5)
		
		# 状态横幅
		self.status_banner = tk.Canvas(self.root, height=30, bg='#e6f3ff', highlightthickness=0)
		self.status_banner.grid(row=1, column=0, sticky='ew', padx=10, pady=5)
		self.banner_text = self.status_banner.create_text(20, 15, anchor='w', font=('微软雅黑', 12, 'bold'), fill='#155724')
		self.status_banner.grid_remove()

		# 初始化变量
		self.start_x = None
		self.start_y = None
		self.screenshot = None
		
		# 绑定快捷键
		self.root.bind("<Control-s>", lambda e: self.save_image())
		self.root.bind("<Control-q>", lambda e: root.quit())

	def fullscreen_capture(self):
		self.status_label.config(text='截取全屏', foreground='blue')
		self.root.update()
		screenshot = pyautogui.screenshot()
		self.show_preview(screenshot)
		self.status_label.config(text='准备就绪', foreground='green')

	def region_capture(self):
		self.status_label.config(text='区域截图', foreground='blue')
		self.root.update()
		self.root.withdraw()
		self.region_window = tk.Toplevel(self.root)
		self.region_window.overrideredirect(True)
		self.region_window.attributes('-alpha', 0.3)
		
		self.region_canvas = tk.Canvas(self.region_window, cursor="cross")
		self.region_canvas.pack(fill="both", expand=True)
		
		self.region_canvas.bind("<ButtonPress-1>", self.start_selection)
		self.region_canvas.bind("<B1-Motion>", self.update_selection)
		self.region_canvas.bind("<ButtonRelease-1>", lambda e: self.end_selection(e))
		
		self.region_window.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")

	def start_selection(self, event):
		self.start_x = event.x_root
		self.start_y = event.y_root
		self.region_canvas.delete("selection_rect")

	def update_selection(self, event):
		if self.start_x and self.start_y:
			self.region_canvas.delete("selection_rect")
			self.region_canvas.create_rectangle(
				self.start_x, self.start_y,
				event.x_root, event.y_root,
				outline="red", tags="selection_rect"
			)

	def end_selection(self, event):
		if self.start_x and self.start_y:
			end_x = event.x_root
			end_y = event.y_root
			self.region_window.destroy()
			self.root.deiconify()
			self.root.after(10, self.capture_area,
						   min(self.start_x, end_x), min(self.start_y, end_y),
						   max(self.start_x, end_x), max(self.start_y, end_y))

	def capture_area(self, x1, y1, x2, y2):
		"""截图指定区域并显示预览"""
		width = x2 - x1
		height = y2 - y1
		self.screenshot = pyautogui.screenshot(region=(x1, y1, width, height))
		self.show_preview(self.screenshot)

	def save_image(self):
		try:
			if self.screenshot:
				file_path = filedialog.asksaveasfilename(defaultextension=".png",
														filetypes=[("PNG", ".png"), ("JPEG", ".jpg")])
				if file_path:
					self.screenshot.save(file_path)
					self.status_label.config(text="保存成功！", foreground='#00ff00', font=('Arial', 12, 'bold'))
					self.status_banner.itemconfig(self.banner_text, text="截图已保存至：" + file_path)
					self.status_banner.config(bg='#d4edda')
					self.status_banner.grid()
					self.root.after(3000, self.status_banner.grid_remove)
				else:
					self.status_label.config(text="保存已取消", foreground='orange')
		except Exception as e:
			self.status_label.config(text=f"保存失败: {str(e)}", foreground='red')

	def clear_preview(self):
		self.canvas.delete("all")
		self.canvas.config(width=0, height=0)
		self.screenshot = None
		self.scale_factor = 1.0
		self.status_label.config(text="预览已清除", foreground='blue', font=('Arial', 10))
		self.status_banner.grid_remove()
		self.root.unbind("<Control-MouseWheel>")

	def show_preview(self, image):
		# 初始化缩放相关变量
		self.scale_factor = 1.0
		self.original_image = image
		
		# 调整画布尺寸并清除旧内容
		self.canvas.config(width=image.width, height=image.height)
		self.canvas.delete("all")
		self.drawn_items = []
		
		# 显示新截图
		self.update_zoom()
		self.screenshot = image
		
		# 绑定缩放事件
		self.canvas.bind("<Control-MouseWheel>", self.on_mousewheel)
		

	def on_mousewheel(self, event):
		# 计算缩放比例
		scale_delta = 0.1 if event.delta > 0 else -0.1
		self.scale_factor = max(0.1, min(3.0, self.scale_factor + scale_delta))
		
		# 更新显示
		self.update_zoom()
		
		# 调整画布尺寸
		self.canvas.config(
			width=int(self.original_image.width * self.scale_factor),
			height=int(self.original_image.height * self.scale_factor)
		)

	def update_zoom(self):
		# 生成缩放后的图像
		new_width = int(self.original_image.width * self.scale_factor)
		new_height = int(self.original_image.height * self.scale_factor)
		resized_image = self.original_image.resize(
			(new_width, new_height),
			Image.Resampling.LANCZOS
		)
		self.preview_image = ImageTk.PhotoImage(resized_image)
		self.canvas.create_image(0, 0, anchor=tk.NW, image=self.preview_image)
		# 启用滚动区域
		self.canvas.config(scrollregion=self.canvas.bbox(tk.ALL))

	def start_draw(self, event):
		# 转换坐标到原始图像尺寸
		self.start_x = int(event.x / self.scale_factor)
		self.start_y = int(event.y / self.scale_factor)

	def end_draw(self, event):
		# 保存当前绘制项
		if self.current_tool in ['rect', 'arrow']: # type: ignore
			item = self.canvas.find_withtag('current')
			if item:
				self.drawn_items.append(item[0])

	def draw_shape(self, event):
		# 转换当前坐标到原始尺寸
		current_x = int(event.x / self.scale_factor)
		current_y = int(event.y / self.scale_factor)
		
		if self.current_tool == 'rect': # type: ignore
			self.canvas.create_rectangle(
				self.start_x * self.scale_factor,  # type: ignore
				self.start_y * self.scale_factor, # type: ignore
				current_x * self.scale_factor,
				current_y * self.scale_factor,
				outline='red'
			)
		elif self.current_tool == 'arrow': # type: ignore
			self.canvas.create_line(
				self.start_x * self.scale_factor, # type: ignore
				self.start_y * self.scale_factor, # type: ignore
				current_x * self.scale_factor,
				current_y * self.scale_factor,
				arrow=tk.LAST, 
				fill='blue'
			)

if __name__ == "__main__":
	root = tk.Tk()
	app = ScreenshotApp(root)
	root.mainloop()