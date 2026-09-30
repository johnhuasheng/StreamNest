$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $repoRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$version = (Get-Content -LiteralPath (Join-Path $repoRoot 'package.json') -Raw | ConvertFrom-Json).version
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'package.json 版本号格式无效' }
$stage = Join-Path $repoRoot "outputs\StreamNest-Windows-x64-$stamp"
$archive = Join-Path $repoRoot "outputs\StreamNest-Windows-x64-v$version.zip"
if (Test-Path -LiteralPath $stage) { throw "发布目录已存在：$stage" }
if (Test-Path -LiteralPath $archive) { throw "发布 ZIP 已存在：$archive" }

$nodeCommand = Get-Command node.exe -ErrorAction Stop
$ffmpegCommand = Get-Command ffmpeg.exe -ErrorAction Stop
$nodeVersion = (& $nodeCommand.Source --version).Trim().TrimStart('v')
$ffmpegBuildRoot = Split-Path -Parent (Split-Path -Parent $ffmpegCommand.Source)
$ffmpegReadme = Join-Path $ffmpegBuildRoot 'README.txt'
$ffmpegLicense = Join-Path $ffmpegBuildRoot 'LICENSE'
if (-not (Test-Path -LiteralPath $ffmpegReadme) -or -not (Test-Path -LiteralPath $ffmpegLicense)) {
  throw 'FFmpeg 安装目录缺少 README.txt 或 LICENSE，不能制作公开发布包。'
}
$readmeText = Get-Content -LiteralPath $ffmpegReadme -Raw
if ($readmeText -notmatch 'Source Code: https://github\.com/FFmpeg/FFmpeg/commit/([0-9a-fA-F]+)') {
  throw '无法确认随包 FFmpeg 的对应源码提交。'
}
$ffmpegCommit = $Matches[1]
$ffmpegVersion = (& $ffmpegCommand.Source -version | Select-Object -First 3) -join "`n"
if ($ffmpegVersion -match '--enable-nonfree') { throw '该 FFmpeg 构建含 nonfree 选项，停止打包。' }

npm ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci 失败' }
npm run typecheck
if ($LASTEXITCODE -ne 0) { throw '前端类型检查失败' }
npm run build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }

$python = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  python -m venv (Join-Path $repoRoot 'backend\.venv')
  if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败' }
}
& $python -m pip install -r (Join-Path $repoRoot 'backend\requirements.txt') 'pyinstaller==6.22.3'
if ($LASTEXITCODE -ne 0) { throw '安装后台依赖失败' }
$resolverDist = Join-Path $repoRoot "build\resolver-dist-$stamp"
$resolverWork = Join-Path $repoRoot "build\resolver-work-$stamp"
$specDir = Join-Path $repoRoot 'build'
New-Item -ItemType Directory -Path $specDir -Force | Out-Null
& (Join-Path $repoRoot 'backend\.venv\Scripts\pyinstaller.exe') `
  --noconfirm --onedir --name streamnest-resolver `
  --distpath $resolverDist --workpath $resolverWork --specpath $specDir `
  --paths (Join-Path $repoRoot 'backend') `
  --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all curl_cffi `
  --log-level WARN (Join-Path $repoRoot 'backend\portable_entry.py')
if ($LASTEXITCODE -ne 0) { throw '后台 EXE 打包失败' }

$sourceArchive = Join-Path $repoRoot "build\ffmpeg-source-$ffmpegCommit.zip"
if (-not (Test-Path -LiteralPath $sourceArchive)) {
  Invoke-WebRequest -Uri "https://github.com/FFmpeg/FFmpeg/archive/$ffmpegCommit.zip" `
    -OutFile $sourceArchive -TimeoutSec 180
}
$nodeLicense = Join-Path $repoRoot "build\node-LICENSE-v$nodeVersion"
if (-not (Test-Path -LiteralPath $nodeLicense)) {
  Invoke-WebRequest -Uri "https://raw.githubusercontent.com/nodejs/node/v$nodeVersion/LICENSE" `
    -OutFile $nodeLicense -TimeoutSec 60
}

New-Item -ItemType Directory -Path $stage | Out-Null
$runtime = Join-Path $stage 'runtime'
New-Item -ItemType Directory -Path $runtime | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot 'dist') -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot 'node_modules') -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot 'app') -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot 'public') -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot 'browser-helper') -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $resolverDist 'streamnest-resolver') -Destination (Join-Path $runtime 'resolver') -Recurse
Copy-Item -LiteralPath $nodeCommand.Source -Destination (Join-Path $runtime 'node.exe')
Copy-Item -LiteralPath $ffmpegCommand.Source -Destination (Join-Path $runtime 'ffmpeg.exe')
Copy-Item -LiteralPath $ffmpegReadme -Destination (Join-Path $runtime 'FFmpeg-README.txt')
Copy-Item -LiteralPath $ffmpegLicense -Destination (Join-Path $runtime 'FFmpeg-LICENSE.txt')
Copy-Item -LiteralPath $sourceArchive -Destination (Join-Path $runtime 'FFmpeg-source.zip')
Copy-Item -LiteralPath $nodeLicense -Destination (Join-Path $runtime 'Node-LICENSE.txt')
foreach ($name in @('package.json','vite.config.ts','next.config.ts','haijiao-domains.json',
    'portable-launcher.mjs','启动StreamNest便携版.cmd','LICENSE','README-便携版.txt','THIRD_PARTY.md')) {
  Copy-Item -LiteralPath (Join-Path $repoRoot $name) -Destination $stage
}

$archiveParent = Split-Path -Parent $stage
$archiveFolder = Split-Path -Leaf $stage
& tar.exe -a -cf $archive -C $archiveParent $archiveFolder
if ($LASTEXITCODE -ne 0) { throw '制作 ZIP 失败' }
$hash = Get-FileHash -LiteralPath $archive -Algorithm SHA256
Write-Output "发布包：$archive"
Write-Output "SHA256：$($hash.Hash)"
