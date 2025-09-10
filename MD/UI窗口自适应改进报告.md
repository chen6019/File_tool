<!-- @format -->

# UI 窗口自适应改进报告

## 修改概述

将图片工具的 UI 窗口默认大小从固定尺寸改为自适应模式，让窗口根据内容自动调整大小，并在屏幕中央显示。

## 具体修改内容

### 1. 移除固定窗口大小设置

**修改位置**: `_setup_ui_config()` 方法

**原始代码**:

```python
self.root.geometry("1600x880")  # 更宽松的默认尺寸
self.root.minsize(1500, 860)   # 提高最小尺寸
```

**修改后**:

```python
# 窗口初始化 - 使用自适应大小
# 不设置固定geometry，让窗口根据内容自适应
self.root.minsize(800, 600)  # 设置合理的最小尺寸
```

### 2. 添加窗口居中功能

**新增方法**: `_center_window()`

**功能特点**:

-   在 UI 完全构建完成后调用
-   计算窗口实际大小和屏幕尺寸
-   将窗口居中显示
-   确保窗口不会超出屏幕边界
-   只设置位置，保持自适应大小

**实现代码**:

```python
def _center_window(self):
    """
    将窗口居中显示

    在UI完全构建完成后调用，让窗口在屏幕中央显示。
    只设置位置，保持窗口的自适应大小。
    """
    try:
        self.root.update_idletasks()  # 确保窗口大小计算完成

        # 获取窗口实际大小
        width = self.root.winfo_width()
        height = self.root.winfo_height()

        # 如果窗口大小还没有正确计算，使用请求的大小
        if width <= 1 or height <= 1:
            width = self.root.winfo_reqwidth()
            height = self.root.winfo_reqheight()

        # 计算居中位置
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)

        # 确保窗口不会超出屏幕边界
        x = max(0, min(x, screen_width - width))
        y = max(0, min(y, screen_height - height))

        # 只设置位置，不设置大小
        self.root.geometry(f"+{x}+{y}")
    except Exception:
        # 静默失败，不影响程序运行
        pass
```

### 3. 调整窗口居中时机

**修改位置**: `_build()` 方法

**添加内容**:

```python
# 延迟进行窗口居中，确保所有组件都已渲染完成
self.root.after(400, self._center_window)
```

**时机安排**:

-   300ms: 设置分栏比例
-   400ms: 窗口居中

## 改进效果

### 优点

1. **自适应性**: 窗口大小根据内容自动调整，适应不同屏幕分辨率
2. **用户友好**: 窗口在屏幕中央显示，提供更好的视觉体验
3. **灵活性**: 用户可以手动调整窗口大小，不受固定尺寸限制
4. **兼容性**: 保持最小尺寸限制，确保界面元素可见

### 保留的功能

1. **最小尺寸限制**: 800x600 像素，确保基本界面可用
2. **自动调整高度**: 预览图片时仍然可以自动调整窗口高度
3. **分栏比例设置**: 左右分栏的比例调整功能保持不变

## 测试结果

-   ✅ 代码语法检查通过
-   ✅ 应用程序成功启动
-   ✅ 窗口自适应显示正常
-   ✅ 界面布局完整

## 技术细节

### 延迟执行策略

由于 Tkinter 的特性，窗口大小的正确计算需要等待组件完全渲染。采用分阶段延迟执行：

1. UI 构建完成
2. 300ms 后设置分栏比例
3. 400ms 后进行窗口居中

### 边界检查

居中算法包含边界检查，确保窗口不会超出屏幕可视区域：

```python
x = max(0, min(x, screen_width - width))
y = max(0, min(y, screen_height - height))
```

### 异常处理

所有窗口操作都包含异常处理，静默失败不影响程序正常运行。

## 总结

此次修改成功实现了 UI 窗口的自适应显示，提供了更好的用户体验，同时保持了原有功能的完整性和稳定性。窗口现在能够根据内容自动调整大小，并在屏幕中央显示，适应不同的屏幕分辨率和用户需求。
