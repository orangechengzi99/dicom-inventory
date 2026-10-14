# 合成演示操作指南（含5分钟讲解提纲）

本演示只说明工具的工程流程，不是医疗诊断或真实临床性能验证。样例是元数据文件，没有患者身份和PixelData，不能用于阅片/重建；SR样例亦不是完整临床SR内容。

本文提供可运行的演示步骤和讲解提纲，不含录制视频。

首次安装见[README](../README.md)；界面按钮、批量处理、取消和历史/复检操作见[用户操作手册](USER_GUIDE.md)。

## 1. 准备（一次）

在已解压/克隆的项目根目录建立Python 3.12环境并安装requirements.txt。以下命令从项目根目录执行，先用`python --version`确认3.12；若机器安装了多个版本，也可用`py -3.12`代替`python`。已有环境无需重复建立。

```powershell
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".\.venv\Scripts\python.exe" -m tools.synthetic_demo create --output ".demo"
& ".\.venv\Scripts\python.exe" -m dicom_inventory
```

记录生成命令打印的DEMO_ROOT、SOURCE和WORK。DEMO_ROOT为`.demo/demo-<自动生成UUID>`，SOURCE/WORK是其中不同的兄弟目录。首次WORK为空，满足软件工作库边界。以下`$demoCase`请赋为刚打印的DEMO_ROOT，不要填患者目录。

## 2. 手动操作演示

| 步骤 | 操作 | 可解释的结果 |
|---|---|---|
| 发现 | 选择SOURCE，后台发现后点击开始扫描 | 11个文件：6 CT、3 MR、1 SR元数据样例、1故意不可读文件 |
| 审阅 | 文件明细选CT_001/CT_003；切Study/Series | 初始5项问题：间距缺失、矩阵不一致、重复UID、SR未评估、读取失败 |
| 记录 | 选择WORK、保存扫描；在处理清单编辑 | 将“间距缺失/矩阵不一致/读取失败”标已处理待复检；重复UID标已确认；SR可忽略须写理由 |
| 基线 | 工作记录→选择复检基线 | 选择刚保存的完整批次（有单文件失败不代表整批扫描没完成） |
| 模拟外部处理 | 软件空闲时运行下方fixed切换 | 仅演示脚本改写它生成的样例；主软件没有修复原DICOM功能 |
| 复检 | 工作记录→按基线重新扫描 | 新发现和新扫描；3项关闭、重复UID仍在、SR忽略记录保留 |
| 导出 | 复检窗口导出对比CSV；主导出菜单选前三类 | 导出到DEMO_ROOT/exports或其他独立目录，不能进SOURCE |
| 历史 | 重启、选择原WORK、打开已保存复检对比 | 不需要再读原样例；对比时状态固定，主审阅显示当前人工状态 |

```powershell
$demoCase = ".demo\demo-<替换为刚生成的UUID>"
& ".\.venv\Scripts\python.exe" -m tools.synthetic_demo switch --demo-root $demoCase --phase fixed
```

fixed只补合成间距、恢复合成矩阵并将同路径不可读样例换成可读元数据。不要在扫描运行中切换，避免演示数据不稳定。

以下三个阶段分别执行。每次切换后都要在软件中完成复检，再继续下一阶段；不能一次执行全部命令后只扫描一次。

### changed：观察新增问题与证据变化

先完成fixed复检，将已保存的fixed完整批次选为新基线；将重复UID标为可忽略，并注明“合成演示暂忽略”。确认软件空闲后，只执行这一条命令：

```powershell
& ".\.venv\Scripts\python.exe" -m tools.synthetic_demo switch --demo-root $demoCase --phase changed
```

回到软件，选择“工作记录 → 按基线重新扫描”。完成后核对新增CT_007、层厚不一致，以及重复UID组因关联元数据改变而恢复待确认。确认此次复检已完成并保存，再把changed完整批次选为下一次基线。

### recurrence：观察已关闭问题再次出现

确认当前基线是changed完整批次，软件空闲后执行：

```powershell
& ".\.venv\Scripts\python.exe" -m tools.synthetic_demo switch --demo-root $demoCase --phase recurrence
```

回到软件，选择“工作记录 → 按基线重新扫描”。完成后核对CT_001的原缺失问题再次出现，同证据的已关闭轨迹重开。确认此次复检已完成并保存，再把recurrence完整批次选为下一次基线。

### removed：观察移除文件后的无法确认

确认当前基线是recurrence完整批次，软件空闲后执行：

```powershell
& ".\.venv\Scripts\python.exe" -m tools.synthetic_demo switch --demo-root $demoCase --phase removed
```

回到软件，选择“工作记录 → 按基线重新扫描”。完成后核对仅合成CT_001被移除，其旧问题应为“无法确认”，不能当作修复成功或据此关闭。

removed只删除生成器拥有且哈希匹配的合成CT_001，切baseline可以重新生成。脚本拒绝非演示目录、修改过的样例、额外文件、软链接/联接和硬链接；遇到拒绝，应保留目录并新建演示。阶段写入不是整批原子事务，写入失败可能留下不完整阶段，需要重新生成演示。

## 3. 自动回放（不操作GUI）

```powershell
& ".\.venv\Scripts\python.exe" -m tools.synthetic_demo replay --output ".demo"
```

回放调用核心、SQLite和导出API，按baseline→fixed→changed→recurrence→removed执行，使用实际扫描时间和状态接口。replay.json记录各批次、比较ID、统计/结论及相对导出位置。WORK含五个批次、四份比较；exports的四个阶段各有四种CSV。生成文件保存在独立演示目录。

演示初始11文件/5问题，fixed11文件/2问题，changed12文件/3问题，recurrence12文件/4问题，removed11文件/3问题。问题数减少不能独自证明修复，必须看复检依据和系统动作。

## 4. 五分钟讲解提纲

- 0:00—0:40：影像数据清点之后还需要解释和跟踪，不是做阅片AI。
- 0:40—1:30：展示五列审阅、Study/Series及问题证据，解释错误/警告/提示和未评估。
- 1:30—2:20：记录人工状态，强调可忽略不删问题，已处理不等于已验证。
- 2:20—3:30：用fixed复检展示关闭3项、仍在1项和忽略1项；导出四种CSV。
- 3:30—4:20：用changed/recurrence展示重新确认和重开，保留审计。
- 4:20—5:00：移除样例仍无法确认；说明只读、离线、元数据范围和测试，不作临床性能承诺。

## 5. 重新截图

先运行replay，再将DEMO_ROOT填入下面命令。工具直接捕获实际Qt窗口，输出目录须不存在同名图片。

```powershell
$demoCase = ".demo\demo-<替换为replay命令刚打印的UUID>"
& ".\.venv\Scripts\python.exe" -m tools.capture_demo --demo-root $demoCase --output "docs\screenshots-new"
```

截图会包含合成来源路径、UID和备注，分享前应核对画面中没有私人信息。六张截图使用offscreen合成演示，涵盖文件、分层、处理目标及三种复检状态，不代替现场使用验证。处理目标图加载历史快照与当前人工记录，当前状态可能不同于历史比较当时的状态。
