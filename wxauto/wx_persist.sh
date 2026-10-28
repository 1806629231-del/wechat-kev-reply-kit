#!/bin/bash
# 微信数据持久化，box 重置后不用重新扫码/重装：snapshot | link | restore | check
# PERSIST_DIR 默认 /workspace/persist/wechat（Grok Bot 的 /workspace 重置后保留）
P=${PERSIST_DIR:-/workspace/persist/wechat}; H=$HOME; DEB=${WECHAT_DEB:-/workspace/wechat.deb}
SUDO=sudo; [ "$(id -u)" = 0 ] && SUDO=
running(){ pgrep -x wechat >/dev/null || pgrep -f /opt/wechat/wechat >/dev/null; }
case "$1" in
snapshot) for d in .xwechat xwechat_files; do [ -L $H/$d ] || { mkdir -p $P/$d; cp -a $H/$d/. $P/$d/ 2>/dev/null; }; done
  cp -f /etc/machine-id $P/machine-id; hostname > $P/hostname; echo snapshot ok ;;
link) running && { echo "微信在运行，先关掉再 link"; exit 1; }
  for d in .xwechat xwechat_files; do [ -L $H/$d ] && continue
    [ -d $H/$d ] && { mkdir -p $P/$d; cp -a $H/$d/. $P/$d/; rm -rf $H/$d; }; mkdir -p $P/$d; ln -sfn $P/$d $H/$d; done; echo link ok ;;
restore|check) C=0; [ "$1" = check ] && C=1; r(){ [ $C = 1 ] && { echo "WOULD: $*"; return; }; "$@"; }
  [ -x /usr/bin/wechat ] || [ -x /opt/wechat/wechat ] && echo "[OK] 微信已装" || { [ -f "$DEB" ] && r $SUDO apt-get install -y -q "$DEB" || echo "[TODO] 没有 $DEB，见 README 下载微信 Linux 版"; }
  if [ -f $P/machine-id ] && ! cmp -s $P/machine-id /etc/machine-id; then r $SUDO cp $P/machine-id /etc/machine-id; else echo "[OK] machine-id"; fi
  for d in .xwechat xwechat_files; do if [ -L $H/$d ]; then echo "[OK] $d 已软链"; elif [ ! -e $H/$d ] && [ -d $P/$d ]; then r ln -sfn $P/$d $H/$d; else echo "[INFO] $d 未软链（微信停着时跑 link）"; fi; done ;;
*) echo "用法: $0 snapshot|link|restore|check";;
esac
