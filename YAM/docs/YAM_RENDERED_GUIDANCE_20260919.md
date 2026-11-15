# YAM 实体渲染指引与固定预览入口

2026-09-19。TASK-YAM-0015 使用 Isaac Sim 5.1.0.0 / Python 3.11 的 RTX 图像作为透明目标图。保持原60条布局、相机内外参、物料组合、上层关闭规则。数据仍为候选，现场对齐、边界、TCP和真实抓取未验收。

## 固定入口

在YAM主控机浏览器固定打开 `http://127.0.0.1:8765/`。原预览服务仍由teleop启动，不是常驻相机进程。页面已打开时保留标签页；停止teleop时提示断开，重新启动后自动加载新的会话，不需要复制新地址。第一次访问且teleop未启动时，浏览器会显示连接失败；启动后刷新一次。

端口由原 `--preview-port` 控制；改变端口需要对应修改地址。通过SSH查看时保持隧道：

```bash
ssh -N -L 8765:127.0.0.1:8765 -p 2222 chenyuan@192.168.77.21
```

然后在查看电脑打开同一localhost地址。服务没有开放到局域网监听。会话token仍随机轮换，旧token在重启后失效；稳定入口通过同源iframe承载当前token页面。bootstrap拒绝非localhost Host、跨站Origin/Fetch和不带同源请求头的会话查询，控制POST仍校验token。启动器先等待保留页面重连，检测到查看者时不再自动新开标签页。

## 实体图启动

在YAM：

```bash
cd /home/chenyuan/workspace/YAM/gello_software
scripts/teleop --recording-format mcap --control-hz 200 --state-hz 200 \
  --task-name TASK-YAM-0015 \
  --guidance-bundle /home/chenyuan/workspace/real2sim/runs/yam15_guidance_rendered_v2
```

先解决主臂Dynamixel无响应等硬件预检问题；换投影图不能修复总线连接。没有启动第二个采集进程、相机进程或仿真器。

页面显示按标定主相机渲染的实体模型，默认50%透明度，背景完全透明，保留左右腕实时图。原token子页面内部相对资源、录制控制和前后切换功能不变。上层抽屉初始关闭，本条三种不同小物品各一件；所有物品放入同一上层抽屉再关上。原按钮/扳机流程、录制开始固定投影、MCAP `/guidance/initial` 和首帧导出不变。

旧线框包 `runs/task_yam_0015_guidance_v1` 保留，需要回看时显式选择旧路径。不会覆盖已有轨迹的快照。

## 外观与验证范围

按登记尺寸和已查看照片构建参数化视觉模型：三层纸柜/上层金色把手、蓝方块、黑白足球、绿黄海绵、粉色纸巾包、白色彩键遥控器、蓝勺。没有运行多视图重建/照片贴图恢复，外观细节与材质是近似。球、勺、遥控器等现在有实际三维形状，不再只是外包络框。

透明度来自同一RTX帧的语义物体mask，不把仿真背景/桌面盖到实时画面上。只展示主相机指引，没有伪造腕相机投影。柜体把手比原柜体包络略向前突出，原抽屉拉出预留区仍需现场核实。

连续切换布局的RTX进程实测会残留上一布局阴影，因此正式发布必须每条独立进程渲染；不能把早期 `yam15_guidance_rendered_v1` 当作最终包。本次最终采用v2，60组独立图，原布局与相机参数逐项一致；每张必须四件物体都有有效mask、保持画面边距，每个独立进程用5个3D测试点比较RTX质心与K/外参的解析投影，1.5px门限。该检查验证投影实现，不是现场标定误差测量。

没有步进物理、没有机械臂/抽屉关节运动，没有证明抓取、拉开或放入成功。YAM只读预渲染PNG，数采环境无需安装Isaac。

## 产物与复现

本机渲染项目：`/mnt/sdb/rhos_collector/real2sim`；YAM项目：`/home/chenyuan/workspace/real2sim`。

最终目录 `runs/yam15_guidance_rendered_v2/`：

- `index.html` / `overview.jpg`：管理者预览。
- `yam15_*.png`：原分辨率640×480 RGBA目标图。
- `yam15_*_preview.jpg`：深色背景预览，不是现场实拍。
- `assets/*.usda`：七件视觉模型，材质来源为照片辅助估计。
- `scene_yam15_*.usda`：独立布局场景，相机/物体与布局对应；assets相对引用。
- `manifest.json`：原布局/物料/标定、运行版本、构建代码及输入哈希。
- `render_report.json`：60个语义mask检查和300个标定投影测试误差。
- `integration_report.json`：YAM原生MCAP离线关联测试。不是硬件测试。

```bash
# 在有统一Isaac 5.1环境的机器生成；output必须全新。
.venv/bin/python scripts/render_yam_drawer_batch.py \
  --source runs/yam_task_0015_preview_20260919 --output runs/yam15_NEW
.venv/bin/python scripts/check_yam_rendered_bundle.py \
  --source runs/yam_task_0015_preview_20260919 --bundle runs/yam15_NEW
```

批处理器通过统一 `scripts/isaac_python.sh` 为每条启动独立5.1进程。构建先写`.building`目录，全部通过后原子改名发布。无需停止原采集服务即可离线构建，但正在运行的teleop不会热替换bundle，下一次按新路径启动生效。

固定入口相关测试：`tests.test_preview_entry`（4项）以及相机/指引/MCAP回归共33项；真实Chromium测试覆盖原标签页保留、指引切换、服务重启token变化后自动重连。测试相机是合成帧，无硬件启动。
