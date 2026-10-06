# snapshot-kwin

KDE Plasma 6 Wayland 上「按下即出」的截图 + 标注 + 简单图片编辑工具。
起因:测试机(Bazzite / KWin 6.7.5 / RTX 5060)上 Spectacle 从按键到出现框选要 0.7–1.4 s。

## 为什么能更快(2026-10-06 实测)

| 环节 | 耗时 |
|---|---|
| KWin `org.kde.KWin.ScreenShot2.CaptureWorkspace`(2560x1440,含读完像素) | **55–75 ms** |
| Spectacle 常驻后按键到框选窗口出现 | 700–800 ms |
| Spectacle 冷启动 | ~1400 ms |

瓶颈在 Spectacle 自身(界面加载),不在 KWin 截图。
其它路线已排除:Flameshot 14 在 KDE 上走 xdg portal(更慢,且需授权);
grim/slurp 依赖 wlr-screencopy / ext-image-copy-capture,KWin 6.7.5 均未实现。

**目标:按键 → 冻结画面框选 ≤ 150 ms。**

## 功能范围(以 Spectacle 标注器为参考)

1. 截图:区域(拖框)、全屏、当前窗口;Esc 取消
2. 标注:自由画笔、荧光笔、直线、箭头、矩形、椭圆、文字、编号贴纸、马赛克/模糊、橡皮
3. 样式:颜色、线宽、填充、字号;撤销/重做
4. 简单编辑:裁剪、旋转、翻转、缩放尺寸、加边框/阴影
5. 输出:复制到剪贴板(默认)、保存到 ~/Pictures/Screenshots、拖出
6. 键位:macOS 习惯(Meta+C 复制、Meta+S 保存、Meta+Z / Meta+Shift+Z 撤销重做、回车完成)

暂不做:滚动长截图(Wayland 下无法替其它窗口注入滚动)、录屏(继续用 Spectacle)。

## 架构

- **常驻守护进程**(systemd user service,仅 Plasma 会话):进程与窗口预先建好、隐藏,
  触发时只做「取图 + 显示」,不付启动成本。
- **取图**:KWin ScreenShot2 D-Bus(受限接口)。授权方式是 `.desktop` 的
  `X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2`,KWin 按调用进程的可执行文件路径匹配,
  所以守护进程用固定路径的专用解释器副本运行,只授权这一份,不授权系统 Python。
- **界面**:Python + GTK4/Adwaita + cairo(系统自带,免安装)。全屏冻结画面上框选,
  松手进入标注编辑器;标注以矢量对象保存,导出时栅格化。
- **触发**:Meta+Alt+1 → 小脚本经 D-Bus 调守护进程(不新起 GUI 进程)。

## 状态

- [x] 可行性:KWin 取图 55–75 ms(`probe` 实测)
- [ ] 守护进程 + 框选(验收:按键到框选 ≤ 150 ms,拟人测试)
- [ ] 标注工具
- [ ] 简单编辑
- [ ] 输出与键位
- [ ] 打包:service / desktop / 快捷键,登记到 repo-mnger
