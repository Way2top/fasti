#!/bin/bash
# Fasti 个人画像 · 一键打开网页
# 双击运行；关闭这个终端窗口即停止服务
#
# 本脚本放在 <项目根>/scripts/ 下，会自动定位到 ../workspace

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT/workspace" || { echo "找不到 workspace 目录"; exit 1; }

echo ""
echo "  ┌────────────────────────────────────────────┐"
echo "  │  Fasti 个人画像                            │"
echo "  │  浏览器会自动打开，没开就复制下面的地址      │"
echo "  │  用完关闭这个窗口即可停止服务                │"
echo "  └────────────────────────────────────────────┘"
echo ""

exec python3 run.py web
