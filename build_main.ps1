# 图片工具 - PowerShell 打包脚本 (仅打包图片工具.py)
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$DebugMode,
    [switch]$Test
)

# 设置控制台编码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "图片工具 - PowerShell 打包脚本" -ForegroundColor Cyan
Write-Host "只打包: 图片工具.py" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查Python环境
Write-Host "[1/5] 检查Python环境..." -ForegroundColor Yellow
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "错误: 找不到虚拟环境，请先创建.venv虚拟环境" -ForegroundColor Red
    Read-Host "按Enter键退出"
    exit 1
}
Write-Host "✓ Python环境正常" -ForegroundColor Green

# 激活虚拟环境并设置环境变量
Write-Host "[2/5] 激活虚拟环境..." -ForegroundColor Yellow
$env:VIRTUAL_ENV = (Resolve-Path ".venv").Path
$env:PATH = "$env:VIRTUAL_ENV\Scripts;$env:PATH"
Write-Host "✓ 虚拟环境已激活" -ForegroundColor Green

# 安装依赖
Write-Host "[3/5] 检查并安装依赖..." -ForegroundColor Yellow
try {
    & ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    & ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
    & ".venv\Scripts\python.exe" -m pip install pyinstaller --quiet
    Write-Host "✓ 依赖安装完成" -ForegroundColor Green
}
catch {
    Write-Host "✗ 依赖安装失败: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# 清理之前的构建文件
if ($Clean) {
    Write-Host "[4/5] 清理旧文件..." -ForegroundColor Yellow
    if (Test-Path "dist") { 
        Remove-Item "dist" -Recurse -Force 
        Write-Host "✓ 已删除 dist 目录" -ForegroundColor Green
    }
    if (Test-Path "build") { 
        Remove-Item "build" -Recurse -Force 
        Write-Host "✓ 已删除 build 目录" -ForegroundColor Green
    }
    Get-ChildItem -Path "." -Filter "*.pyc" -Recurse | Remove-Item -Force
    if (Test-Path "__pycache__") { 
        Remove-Item "__pycache__" -Recurse -Force 
        Write-Host "✓ 已清理缓存文件" -ForegroundColor Green
    }
}

# 构建选项
$buildOptions = @("--clean", "--noconfirm")
if ($DebugMode) {
    $buildOptions += "--debug=all"
    Write-Host "调试模式已启用" -ForegroundColor Yellow
}

# 打包图片工具.py
Write-Host "[5/5] 打包图片工具.py..." -ForegroundColor Yellow
try {
    $arguments = @("-m", "PyInstaller") + $buildOptions + @("build_main.spec")
    $process = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList $arguments -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -eq 0) {
        Write-Host "✓ 图片工具打包成功" -ForegroundColor Green
    } else {
        Write-Host "✗ 图片工具打包失败，退出代码: $($process.ExitCode)" -ForegroundColor Red
        exit 1
    }
}
catch {
    Write-Host "✗ 打包过程出现异常: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "打包完成！" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查构建结果
$exePath = "dist\图片工具.exe"
if (Test-Path $exePath) {
    $fileSize = (Get-Item $exePath).Length
    $fileSizeMB = [math]::Round($fileSize / 1MB, 2)
    Write-Host "✓ 程序打包成功" -ForegroundColor Green
    Write-Host "  文件路径: $exePath" -ForegroundColor White
    Write-Host "  文件大小: $fileSizeMB MB" -ForegroundColor White
    
    # 测试程序（如果指定了-Test参数）
    if ($Test) {
        Write-Host ""
        Write-Host "开始测试程序..." -ForegroundColor Yellow
        try {
            $testProcess = Start-Process -FilePath $exePath -PassThru
            Start-Sleep -Seconds 2
            if (-not $testProcess.HasExited) {
                Write-Host "✓ 程序启动成功" -ForegroundColor Green
                $testProcess.CloseMainWindow()
                Start-Sleep -Seconds 1
                if (-not $testProcess.HasExited) {
                    $testProcess.Kill()
                }
            } else {
                Write-Host "✗ 程序启动失败" -ForegroundColor Red
            }
        }
        catch {
            Write-Host "✗ 程序测试异常: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    
} else {
    Write-Host "✗ 程序打包失败，未找到输出文件" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "生成的文件：" -ForegroundColor Green
Write-Host "- 图片工具: dist\图片工具.exe" -ForegroundColor White
Write-Host ""
Write-Host "用法说明：" -ForegroundColor Cyan
Write-Host "- 双击 exe 文件即可运行" -ForegroundColor White
Write-Host "- 无需安装 Python 环境" -ForegroundColor White
Write-Host "- 支持所有原有功能" -ForegroundColor White

Write-Host ""
$openFolder = Read-Host "是否打开输出文件夹? (y/N)"
if ($openFolder -match "^[Yy]") {
    if (Test-Path "dist") {
        Invoke-Item "dist"
    }
}
