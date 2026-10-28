#!/usr/bin/env bash
# 一键编排 box 侧全部安装（不会扫码、不会打开发送、不会启动保活）。可重复运行。
#   bash install-all.sh           按顺序：box 网络栈(Tailscale/proxychains/ts-watchdog/WX_DISPLAY) → 微信代理启动器
#                                 → 回复层(kev/LLM/闸门/发送/任务) → weflow 聊天记录检索 → 总自检
#   bash install-all.sh --check   只读自检
# 先决：~/secrets/phone-socks.env 已填好国内出口（getting-started 第 4 步由 Bot 远程或手动完成）
set -u; K=$(cd "$(dirname "$0")" && pwd); SECRETS=${SECRETS:-$HOME/secrets/phone-socks.env}
check(){
  echo "######## 1/4 box 网络栈 ########"; bash "$K/box-stack/install.sh" --check --secrets "$SECRETS"; A=$?
  echo "######## 2/4 国内出口代理 ########"; bash "$K/proxy/install.sh" --check --secrets "$SECRETS"; B=$?
  echo "######## 3/4 微信数据持久化 ########"; bash "$K/wxauto/wx_persist.sh" check
  echo "######## 4/4 回复层 + 检索 ########"; bash "$K/install.sh" --check; C=$?
  [ $A$B$C = 000 ] && echo "==== 全部通过 ====" || echo "==== 有缺项：box=$A proxy=$B bot=$C ===="; return $((A|B|C))
}
[ "${1:-}" = --check ] && { check; exit $?; }
[ -f "$SECRETS" ] || { mkdir -p "$(dirname "$SECRETS")"; install -m 600 "$K/box-stack/.env.example" "$SECRETS"; echo "已生成 $SECRETS（chmod 600），先填好国内出口再重跑"; exit 2; }
bash "$K/box-stack/install.sh" --secrets "$SECRETS" --no-bootstrap || { echo "box 网络栈失败，停"; exit 1; }
bash "$K/proxy/install.sh" --secrets "$SECRETS" || { echo "代理层失败，停"; exit 1; }
bash "$K/install.sh" || exit 1
bash "$K/scripts/install-weflow.sh" || echo "weflow 安装失败：之后会退回截屏 OCR"
echo "微信还没装的话：下载官方 Linux .deb 到 /workspace/wechat.deb，sudo apt-get install -y /workspace/wechat.deb；只用 touch ~/.wx_proxy_enabled && ~/bin/wechat-launch.sh 启动"
check; exit $?
