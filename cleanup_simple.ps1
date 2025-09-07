# 项目清理脚本 - 删除非必要文件（简化版）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "项目文件清理脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 要删除的具体文件和目录（只删除项目根目录的）
$deleteItems = @(
    # 构建相关文件（保留最后的打包脚本 build_main.ps1）
    "build",
    "build.bat",
    "build.ps1", 
    "build_all.bat",
    "build_gui.spec",
    "build_single.spec",
    "BUILD_README.md",
    
    # 安装包相关
    "installer.iss",
    "make_portable.bat",
    "make_portable.ps1",
    "portable",
    
    # 测试脚本
    "start.bat",
    "test_exe.bat", 
    "test_exe.ps1",
    
    # 清理脚本本身
    "cleanup.ps1",
    
    # 其他Python文件（保留图片工具.py、截图.py、批量编码转换.py）
    "图片格式转换.py",
    "批量重命名.py",
    "统一工具窗口.py",
    "重复图片清理.py",
    
    # 打包总结
    "打包完成总结.md"
)

Write-Host "将要删除的文件和目录：" -ForegroundColor Yellow
Write-Host ""

$itemsToDelete = @()
foreach ($item in $deleteItems) {
    if (Test-Path $item) {
        $itemsToDelete += $item
        if (Test-Path $item -PathType Container) {
            Write-Host "  📁 $item\" -ForegroundColor Gray
        } else {
            Write-Host "  📄 $item" -ForegroundColor Gray
        }
    }
}

if ($itemsToDelete.Count -eq 0) {
    Write-Host "没有发现需要删除的文件。" -ForegroundColor Green
    Read-Host "按Enter键退出"
    exit 0
}

Write-Host ""
Write-Host "将保留以下核心文件：" -ForegroundColor Green
Write-Host "  📄 图片工具.py (主程序源码)" -ForegroundColor White
Write-Host "  📄 截图.py (截图脚本)" -ForegroundColor White
Write-Host "  📄 批量编码转换.py (编码转换脚本)" -ForegroundColor White
Write-Host "  📄 build_main.ps1 (打包脚本)" -ForegroundColor White
Write-Host "  📄 build_main.spec (打包配置)" -ForegroundColor White
Write-Host "  📄 README.md (项目说明)" -ForegroundColor White
Write-Host "  📄 requirements.txt (依赖列表)" -ForegroundColor White
Write-Host "  📄 .gitignore & .gitattributes (Git配置)" -ForegroundColor White
Write-Host "  📄 dist\图片工具.exe (打包后的可执行文件)" -ForegroundColor White
Write-Host "  📄 图片工具便携版.zip (分发压缩包)" -ForegroundColor White
Write-Host "  📁 .git\ (Git版本控制)" -ForegroundColor White
Write-Host "  📁 .venv\ (Python虚拟环境)" -ForegroundColor White

Write-Host ""
$confirm = Read-Host "确定要删除上述文件吗？这个操作不可撤销！(输入 'yes' 确认)"

if ($confirm -ne "yes") {
    Write-Host "操作已取消。" -ForegroundColor Yellow
    Read-Host "按Enter键退出"
    exit 0
}

Write-Host ""
Write-Host "开始清理..." -ForegroundColor Yellow

$deletedCount = 0
$errorCount = 0

foreach ($item in $itemsToDelete) {
    try {
        if (Test-Path $item -PathType Container) {
            Remove-Item $item -Recurse -Force
            Write-Host "✓ 已删除目录: $item" -ForegroundColor Green
        } else {
            Remove-Item $item -Force
            Write-Host "✓ 已删除文件: $item" -ForegroundColor Green
        }
        $deletedCount++
    }
    catch {
        Write-Host "✗ 删除失败: $item - $($_.Exception.Message)" -ForegroundColor Red
        $errorCount++
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "清理完成！" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "统计信息：" -ForegroundColor Green
Write-Host "  ✓ 成功删除: $deletedCount 项" -ForegroundColor White
if ($errorCount -gt 0) {
    Write-Host "  ✗ 删除失败: $errorCount 项" -ForegroundColor Red
}

Write-Host ""
Write-Host "项目结构现在更加简洁：" -ForegroundColor Green
Write-Host ""

# 显示清理后的目录结构
Get-ChildItem -Path . -Force | Where-Object { -not $_.Name.StartsWith('.venv') } | ForEach-Object {
    if ($_.PSIsContainer) {
        if ($_.Name -eq '.git') {
            Write-Host "  📁 $($_.Name)\ (Git版本控制)" -ForegroundColor Cyan
        } elseif ($_.Name -eq 'dist') {
            Write-Host "  📁 $($_.Name)\ (编译输出)" -ForegroundColor Cyan
        } else {
            Write-Host "  📁 $($_.Name)\" -ForegroundColor Cyan
        }
    } else {
        switch ($_.Extension) {
            '.py' { Write-Host "  🐍 $($_.Name)" -ForegroundColor Blue }
            '.md' { Write-Host "  📄 $($_.Name)" -ForegroundColor White }
            '.txt' { Write-Host "  📄 $($_.Name)" -ForegroundColor White }
            '.exe' { Write-Host "  🚀 $($_.Name)" -ForegroundColor Green }
            '.zip' { Write-Host "  📦 $($_.Name)" -ForegroundColor Green }
            default { Write-Host "  📄 $($_.Name)" -ForegroundColor Gray }
        }
    }
}

Write-Host ""
Write-Host "可分发文件：" -ForegroundColor Yellow
if (Test-Path "dist\图片工具.exe") {
    $exeSize = [math]::Round((Get-Item "dist\图片工具.exe").Length / 1MB, 2)
    Write-Host "  🚀 dist\图片工具.exe ($exeSize MB)" -ForegroundColor Green
}
if (Test-Path "图片工具便携版.zip") {
    $zipSize = [math]::Round((Get-Item "图片工具便携版.zip").Length / 1MB, 2)
    Write-Host "  📦 图片工具便携版.zip ($zipSize MB)" -ForegroundColor Green
}

Write-Host ""
Write-Host "如需重新打包，可以运行：" -ForegroundColor Cyan
Write-Host "  pyinstaller --onefile --windowed 图片工具.py" -ForegroundColor White

Write-Host ""
Read-Host "按Enter键退出"
