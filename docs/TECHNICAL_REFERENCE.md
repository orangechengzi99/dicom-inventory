# 技术参考

本文说明检查范围、工程容差、状态与事务、CSV结构和Python接口。按界面操作请阅读[用户操作手册](USER_GUIDE.md)，安装与快速体验见[README](../README.md)。

## 扫描快照与本地存储

窗口底部的“工作记录”菜单提供选择工作目录、保存扫描、打开历史和问题处理清单：

1. 选择独立工作目录：首次使用选一个已创建的空文件夹，不能是当前DICOM目录或其子目录。已有工作库请选择原工作目录。没有选数据源时也要求首次目录为空，避免误写原数据或个人文件目录。
2. 保存本次扫描：后台将本批次状态、已读文件元数据、问题及证据、逐规则状态、Study/Series汇总保存到 `dicom_inventory.sqlite3`，不受当前屏幕筛选影响。已取消批次亦可保存，保留未完成状态；不会复制或读取原始像素；同批次重复保存拒绝，不覆盖历史。
3. 打开已保存扫描：重启后重新选择同一工作目录，再选择历史记录；完整来源可选择复制，扫描时间按电脑本地时区显示并标注偏移，原时间/批次信息仍可查看。恢复文件/树和问题关联；即使原目录离线也能查看。替换未保存结果前询问，失败保留当前结果。

打开的是“历史快照”，不是重新扫描：界面明确标识历史及未重新读取原文件。“开始扫描”不会直接使用旧文件清单；要重新扫描需重新选择/确认数据目录，重新发现候选文件。历史批次的完成/未完成状态原样保留。单纯保存或打开历史不执行复检，也不自动关闭问题；前后比较请使用下方V1.4流程。

数据库为本地SQLite，schema版本3、观察和对比payload版本1；支持空库0→1→2→3及已有本工具v1/v2库的事务升级，旧观察快照和人工记录不改，升级失败完整回滚。拒绝外部非空数据库、不支持的结构或较新版本，不自动降级。每批次跨表写入在一笔事务中；加载使用一致读快照，校验字段类型/引用/分组/数量，不执行pickle或库中代码。普通扫描保存仍是手动操作；V1.4复检入口会保存本次扫描再比较。工作目录不写全局配置，重启后需重新选目录。旧软件不能打开schema 3库，不提供降级。

保存仅使用固定字段白名单，不读取或保存PatientName、PatientID、出生日期或PixelData。路径、UID及既有文本仍可能识别来源，这不是匿名化或加密工具；不要上传真实工作库到公开仓库。已加入SQLite及事务附属文件忽略规则，忽略不等于匿名化。不支持多机同步、历史删除、自动备份或旧模型字段的任意跨版本转换。

以下接口片段说明调用方式，不是可直接运行的完整脚本。使用前先按“在其他程序中调用核心逻辑”一节导入函数并扫描：`source`是数据源目录的Path，`result`是包含ScanRun的InventoryResult。工作目录须已存在且首次为空；使用已有工作库时选择原工作目录。

```python
from dicom_inventory.storage import SnapshotStore

store = SnapshotStore(r"D:\dicom_workspace", source_root=source)
run_id = store.save(result)  # result须包含source_root明确的ScanRun
saved_runs = store.list_scans()
restored = store.load(run_id)  # 只读工作库，不重新读DICOM
```

## 复检匹配与状态事务

1. 完成一次扫描，选择独立工作目录并保存。核对问题后，可记录“已确认”“可忽略”或“已处理待复检”及备注；软件不会替你修复原文件。
2. 在“工作记录 → 选择复检基线…”选择已保存的完整批次。
3. 选择“按基线重新扫描”：重新发现同一来源目录，不复用历史候选清单。发现、读取、校验、保存和比较均在后台；扫描中可以安全取消。
4. 完成后自动保存新批次及比较结果，打开复检清单。可切换问题、文件和数量变化，按结论筛选，查看原因、两批关联文件、证据及实际规则执行情况。
5. 在主窗口选择“导出 CSV → 复检对比 CSV”，或在复检窗口点击“导出对比 CSV”。关闭软件后选回同一工作目录，从“打开已保存复检对比…”重开，无需原DICOM目录在线。历史对比不会重新扫描或重放状态变更。

也可先选择基线，完成普通新扫描后使用“对比当前扫描…”。同一批次不能对比自己；基线规则版本与当前软件不同，禁止按它重扫验证旧问题。改变工作目录会清空旧基线。扫描失败显示失败，不生成完成结论；取消批次可保存并查看“无法确认”的对比，但不能用于关闭。

对比结论与人工处理状态分开：

| 结论 | 含义与处理 |
|---|---|
| 新出现 | 本次观察新增；建立自己的待确认轨迹 |
| 仍然存在 | 同一问题及证据仍出现；保留原处理状态和备注 |
| 已解决 | 关联规则有充分本次检查依据且不再报告；不自动等于“已关闭” |
| 已忽略 | 原人工忽略仍适用；问题和审计保留，观察仍记录存在或消失 |
| 需重新确认 | 问题证据、关联范围或所选元数据变化；不沿用旧确认/忽略结论 |
| 无法确认 | 范围、时间、对象身份或规则执行不足；未再次出现不证明解决 |
| 再次出现 | 原已关闭的相同证据问题在完整可比复检中再次出现；恢复待确认 |

自动关闭仅针对“已处理待复检”，且必须满足：两批完整、同源同规则、时间不重叠、本次范围可靠且无读取失败、原关联文件仍在、已知对象身份可对齐、相关规则实际适用并通过。序列问题还核对基线整个序列文件范围。补入无效UID/矩阵/间距/位置值不算解决；删除、改名、归组/对象替换、规则跳过等保留无法确认原因。已解决的待确认/已确认问题不替人关闭，可忽略问题仍保留忽略及审计。

关闭、重开及重新确认均保留备注，追加系统审计，和对比报告在同一SQLite事务内提交；任一变更失败全部回滚。状态变更只基于最新同源批次，且人工处理必须早于该次扫描。较旧扫描或扫描后才添加的处理标记不触发系统变更。同一批次对重复比较返回原报告，不再次关闭/重开。

证据变化若属于同一处理轨迹，会恢复待确认；若问题payload不同，则创建新的待确认轨迹，旧记录/理由/审计不改写。已关闭记录只能查看备注及审计，不能人工编辑；完整可比复检确认相同轨迹再次出现才重开。普通保存、部分或不可比对比不会系统转换状态。

对比窗口和CSV的“对比时状态”是当时提交的固定记录；主界面处理入口显示当前状态，之后人工编辑不会改写历史报告。数量包含文件、成功/失败、Study、Series、唯一实例和各级问题计数；不可比批次只可参考已读范围。

文件按相同来源根目录下的相对路径匹配，改名/移动表现为减少和新增。只比较已保存的白名单元数据及读取状态；没有全文件指纹，不检测像素或所有内容变化，也不保证扫描期间文件未改变。新增文件不必然有新问题，文件减少也不等于原问题解决。

复检是“保存新扫描 → 保存比较及系统变更”两笔事务。第二步失败可能已保留新扫描，界面明确提示，当前结果不丢失；核对工作库后用“对比当前扫描…”重试，复用该已保存批次。没有自动备份、跨机同步或所有磁盘故障恢复保证。

GUI无关调用片段：`store`是已初始化的工作库；`baseline_id`和`current_id`是其中两个已保存批次的ID，`baseline`和`current`是分别加载的InventoryResult；`output_dir`是独立输出目录的Path。纯计算预览不更新人工状态，保存比较才执行符合条件的系统变更。

```python
from dicom_inventory.core.comparison import compare_scans
from dicom_inventory.core.comparison_export import export_comparison_csv

preview = compare_scans(baseline, current)  # 纯计算，不写库、不改变人工状态
report = store.compare_and_save(baseline_id, current_id)  # 事务保存报告及符合条件的系统变更
restored_report = store.get_comparison(report.comparison_id)
comparison_path = export_comparison_csv(report, baseline, current, output_dir)
```

## 取消与部分批次

发现文件、读取元数据、质量检查或汇总时，点击数据目录右侧的“取消扫描”。按钮随即变为“正在停止…”，等待安全检查点；正在执行的单次文件系统访问或pydicom读取需要先返回。停止后保留已读文件，显示“扫描已取消 · 未完成批次”。即使所有文件已经读完，只要检查/汇总被取消，仍是未完成；若最后检查点已经通过，之后的取消请求不会倒改真实完成结果。

部分结果只包含已经尝试读取的记录，失败数不包含尚未读取的文件；已读数量、已知候选数量及停止阶段保存在批次说明，悬停概览可查看。发现过程中取消时目录总数未知，保留零读的已取消批次。取消后会重新整理文件级事实及已读集合中的SOP重复观察，清除中断的序列告警与空间排序，所有序列检查记录为未评估；不能用少量问题或没有问题证明通过。发现目录不可访问等范围不完整时，也不进行完整序列推断。

可以从“工作记录”保存部分结果，重开时保留状态、范围、问题和人工审计。取消不删除问题、不自动关闭任何记录。再次扫描请重新选择目录，或在原目录输入框按回车：系统重新发现文件，不沿用部分候选清单，也不是断点续扫。

三种CSV及含人工状态的问题CSV，在批次未完成时自动使用 `partial_` 前缀，并生成与CSV同名的 `.scan.json`（如 `partial_issues.csv.scan.json`）。说明包含批次ID、时间、状态、已读计数、规则版本和范围限制；应连同CSV保留。原CSV列顺序、UTF-8-SIG、防覆盖和独立目录限制不变，完整批次命名不变。文件CSV的Status仍表示读取状态，“成功”不表示完成全部质量检查。CSV和说明不是一个原子写入事务；说明写入失败不会报告导出成功，可能留下明确带partial前缀的文件，请核对后重新导出。

取消只适用于发现与扫描，工作库事务和导出任务需等待完成。部分结果整理仍在后台运行；大目录、慢磁盘或网络文件系统可能需要等待，没有瞬时中断或固定停止时限。任务完成后才允许关闭软件。

GUI无关调用片段：先从`dicom_inventory.core`导入`discover_dicom_files`和`inventory_files`，将`source`设为数据源目录的Path。在其他线程调用 `token.request()`，将同一标记传入发现与扫描。

```python
from dicom_inventory.core.cancellation import CancellationToken

token = CancellationToken()
files = discover_dicom_files(source, cancel_check=token.checkpoint)
result = inventory_files(files, source_root=source, cancel_check=token.checkpoint)
# 已取消的发现结果会生成零读、已取消批次，不继续读取。
```

新增“Study / Series 分层”展示检查→序列→文件，可选择检查或序列查看汇总、问题和规则执行状态。范围下拉框按Study/Series限制两种视图；树与范围菜单的序列编号一致，但编号只属于当前批次，不是DICOM标识。问题关联列表可定位文件，“定位所属序列”可返回其序列；定位会清除筛选。未知/非法归属与读取失败逐文件建立未归组分支，不合并空UID。冲突Series UID在各Study下显示各自文件，右侧明确本分支与全UID范围的计数，不重复算扫描文件。

## 人工处理与审计

先选择独立工作目录并“保存本次扫描”，再使用右侧“处理记录…”编辑当前问题；或在“工作记录 → 问题处理清单…”用Ctrl/Shift选择多项并点击“编辑所选问题”。状态针对结构化问题，同一问题关联多个文件时共享一条处理记录，不是给每个文件改扫描状态。

- 待确认：尚需人工判断；默认状态。
- 已确认：人工确认该问题或已核对其事实，不表示文件通过检查。
- 可忽略：必须填写理由；保留问题、证据及审计，不从异常统计中移除。
- 已处理待复检：人工记录已处理，尚没有自动验证，不算已解决。
- 已关闭：由V1.4有依据的复检自动设置；人工入口不能设置或改写，可以查看备注和审计。

四种人工状态间允许重新选择，任何实质状态/备注变化都追加审计；重复保存相同内容不制造新修订。备注最多2000字，批量默认保留每项原备注；只有勾选“批量替换备注”才统一覆盖（清空亦为明确操作）。单条编辑可查看首次/最近发现批次、更新时间及每次变更的原/新状态和原/新备注；时间按电脑本地时区显示偏移。编辑未保存退出先确认；后台保存期间禁重复提交和关闭；失败保留输入，可检查目录后重试。过期修订整体拒绝，请先复制未保存备注，退出并重新打开清单核对，避免覆盖另一窗口的修改。

所有批量修改在同一事务中，任一项失败不部分提交；已提交的记录可在重启后选同一工作目录、打开原扫描恢复。读取人工记录失败时保留扫描结果，不显示旧缓存为最新状态；从“工作记录 → 问题处理清单…”重试。

处理轨迹保守按“规范来源目录＋扫描规则版本＋稳定问题键＋完整问题payload（含证据、受影响文件和说明）”隔离。只有以上完全相同的观察才沿用人工状态；不同数据集、规则或问题证据建立新的待确认记录，旧备注和审计保留。首次/最近批次按扫描时间而非保存顺序计算，范围仅限该条同证据轨迹。目录迁移不自动继承。此机制不是复检或完整数据变化检测；问题payload未反映的文件内容变化不能据此检出。

历史快照的扫描观察固定；其中显示的人工状态是该同证据轨迹当前状态，而非扫描时状态，旧状态在审计中查看。V1.4固定保存前后对比，但不把文件移除当修复证明。SQLite未加密，审计不是防篡改签名，没有用户账户/操作者身份验证或多机同步。备注是自由文本，无法自动保证排除身份信息；请勿填写患者姓名、患者编号、生日等。分享数据库、截图或CSV前仍须人工核对路径、UID和备注，不要公开真实工作库。

已保存批次的GUI“问题清单CSV”保留原13列并追加HandlingState、HumanNote、HandlingUpdatedAt、HandlingRevision、FirstSeenRun、LatestSeenRun、HandlingTrackKey、ExportScanRun。导出在后台直接读取工作库最新已提交状态，不使用界面缓存；UTF-8-SIG、独立目录、防覆盖与公式保护继续有效。未保存扫描及原核心export_issues_csv API仍输出原13列，不伪造持久化状态；文件与序列CSV不变。

GUI无关状态API片段：`store`是已初始化的工作库，`result`是已保存且包含问题的扫描结果。以下批量示例要求处理记录非空且没有已关闭记录；`expected`携带读取时的轨迹键和修订号，用于拒绝过期更新。实际调用应先选择要处理的问题，再提交对应的记录。

```python
records = store.get_handling(result.scan_run.run_id)
expected = {key: (item.track_key, item.revision) for key, item in records.items()}
records = store.update_handling(result.scan_run.run_id, expected, "已确认", "人工核对说明")
events = store.get_history(result.scan_run.run_id, next(iter(records)))
# note=None 保留各自备注；"" 为显式清空。已关闭被拒绝。
```

## 对象范围与问题级别

首版影像字段规则仅支持SOPClassUID为传统 CT Image Storage（1.2.840.10008.5.1.4.1.1.2）且Modality为CT，或传统 MR Image Storage（1.2.840.10008.5.1.4.1.1.4）且Modality为MR；NumberOfFrames必须未提供或为1。增强CT/MR、多帧、SC、SR、封装PDF、波形、其他类及分类信息不匹配，记录为未评估，不对它们套用传统影像几何/灰度参数缺失规则。DICOMDIR不是影像实例，不检查它的影像/SOP实例必填字段；其目录记录也未被评估。

读取失败和非空SOP UID重复检测适用于所有候选实例；未知Storage对象仅检查最小SOP分类/标识是否有值，并明确未评估影像字段。不是所有对象的完整必填字段校验。

| 级别 | 本版规则口径 | 用户动作 |
|---|---|---|
| ERROR | 读取失败、原关键字段缺失；提供的UID形式无效；有效Series UID跨Study冲突；支持对象矩阵数值无法解释 | 核对原数据、读取权限和导出设置 |
| WARNING | 原空间字段缺失/重复SOP UID；模态/矩阵/间距/层厚/方向不一致；所选数值或几何约束无效；序号/位置重复、空间参照冲突、间距明显变化 | 核对用途、协议和全部关联文件，不自动认定损坏 |
| INFO | 层厚或序号无值、对象未进入影像范围、所选整数序号之间未出现区间 | 阅读限制；序号不连续不证明缺片 |

“关键/建议/不适用”是本工具所选字段的用途策略，不是完整DICOM Type 1/2/条件字段映射。传统CT的RescaleSlope/Intercept与MR分开处理，不要求MR普遍提供这两项。层厚和序号允许空值，提示不证明不合规。重复UID为WARNING，因为本版未比较内容，不能据此认定为可删除副本。

### 序列规则、容差与未评估

UID形式检查覆盖已提供的Study/Series/SOP/SOPClass/FrameOfReference UID：ASCII数字点分段、无多余前导零、最长64字符，不验证注册归属。有效Series UID关联多个有效Study时产生一条ERROR，关联所有文件；模态比较只针对提供的值。矩阵、间距、层厚、方向和序号规则要求同一有效Study/Series、全部为支持的传统单帧CT/MR；混入不支持对象时跳过图像序列推断。重复SOP检查为全候选实例范围，跨序列重复仍保留全部关联。

| 检查 | 工程判定口径 |
|---|---|
| Rows/Columns一致性 | 整数精确比较；缺失或无效时不声称完成一致性检查 |
| PixelSpacing、SliceThickness一致性 | 各分量最大差不超过max(0.001mm,最大绝对值的0.1%)；字符串格式差异不算数值差异 |
| 方向有效性和一致性 | 行列向量长度接近1且正交，误差≤0.0001；同组方向分量最大差≤0.0001 |
| InstanceNumber | 有效整数值可检测重复；全部提供时核对相邻整数间的缺号区间，不要求从1开始或按序号空间排序 |
| 空间排序 | 共同有效FrameOfReferenceUID、全部方向/位置有效且方向一致、无显式LOCALIZER/SCOUT；位置投影到归一化行列叉积法向 |
| 重复位置 | 上述空间条件成立且至少两文件，三维欧氏距离≤0.01mm；不是重复像素结论 |
| 层间距 | 至少三文件，无投影间隔≤0.01mm；与中位间距偏差超过max(0.1mm,中位间距5%)产生WARNING |

空间范围是该统一法向上的位置最小/最大投影（mm），不是病灶范围、三维包围盒或重建结果。SliceThickness是标称层厚，不拿它代替相邻中心间距。缺号、位置重复、间距变化均需结合采集协议；MR可能有不同时间点/回波，合法可变间距也可能触发提示。本版不读取时间点、回波等采集维度，不能保证判断序列完整性，也不能从现有标签发现所有错放Study/Series的文件；只报告可验证的标识冲突或一致性线索，不确定应该移走哪个文件。

PixelSpacing通常须正数；对应Rows或Columns为1时允许该方向间距为0。所选浮点运算额外限制绝对值≤10^12以防极端数值溢出，这是工具安全范围，不是DICOM数值范围规定。未进行完整VR长度、所有字段取值、组织注册或像素检查。

规则执行情况保存在`RuleAssessment`，分为已检查、发现问题、未评估（附原因）。缺失、分类/归属冲突、非平行、空间参照不确定、定位像或样本不足均不冒充通过。单文件无法评估重复位置/间距，汇总会显示“部分未评估”；文件“已检查”不意味着其全部序列规则通过，详情列出实际执行情况。跳过原因不额外生成一批相同INFO问题。数值容差是当前固定工程策略，未做临床校准或GUI可配置。

依据：[UID编码规则](https://dicom.nema.org/medical/dicom/current/output/chtml/part05/chapter_9.html)、[图像位置与方向](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.7.6.2.html)、[InstanceNumber定义](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.7.6.html)、[像素间距例外](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_10.7.html)。上述告警分级/容差是本工具策略，并非标准规定的数据质量诊断阈值。

规则依据：[DICOM CT模块](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.2.html)、[MR模块](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.3.html)、[Image Plane模块](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.7.6.2.html)。只核对所选字段，软件分级是工程用途策略，不是医疗结论。

## 问题模型与稳定键

`QualityIssue`由GUI无关的核心模块生成：键、规则代码/版本、类型、级别、所有关联Study/Series UID和文件、说明、影响与核对建议。一项缺失字段对应一项文件级问题；一组重复SOP UID对应一项组级问题。

V1.2增加作用层级和结构化证据；每条序列规则在全Series UID范围最多一条问题，重复位置/序号关联实际重复文件，其余一致性或归属冲突关联该组全部文件。证据长集合预览最多12项并给出总数，GUI字段汇总预览最多20个不同值；series.csv保留其已导出字段的完整值集合，文件CSV仅提供下文19列的逐文件值，并不包含全部已读取元数据。SOPClassUID需在GUI技术信息中查看；FrameOfReferenceUID和ImageType的逐文件值亦在GUI查看，序列CSV提供的是集合而非逐文件对应关系。所有关联文件不会抽样。序列与Study问题数按问题ID去重，一个跨组问题可在多个汇总中出现，各层问题数不能直接相加。

键是规则版本、规则代码和作用范围的SHA-256，不使用随机批次ID或数据库自增ID。文件级范围优先使用相对数据源根目录的规范化路径，未提供根目录时使用绝对路径；SOP重复组范围使用SOP UID，序列规则范围使用Series键。相同数据集、同一规则版本、相同相对路径下重复扫描/更换顺序可保持键稳定。目录整体迁移可通过明确提供新根目录保持相对键；改文件名、改变规则版本会改变键。键仅在同一数据集内有意义，不是跨数据集全局身份；V1.4比较还核对来源、规则、完整证据及执行范围，不能仅凭同键自动关闭。

`ScanRun`记录批次、UTC起止时间、数据源、规则版本、完成状态及统计，可手动保存到独立工作库并重开。发现时目录不可访问或无扩展名文件头无法检查，会保留“未完成”证据。“完成”仅指已发现候选清单被处理，不证明目录中所有文件都是DICOM、扫描过程中数据不变或文件质量通过。未预期的任务失败与主动取消不同：失败时尚未整理的流式记录仅供查看，不作为可保存/导出的有效批次。

当前规则版本为`metadata-1.2`，与V1.1问题键不兼容。序列键使用有效Series UID；无法归组时用规范化相对路径逐文件后备；不把证据或扫描顺序拼入问题键。复检同时核对证据变化与规则执行情况，不能仅凭同键认定问题已解决。

## 文件识别与统计口径

候选文件包括：

- 扩展名为 `.dcm` 或 `.dicom` 的文件，扩展名不区分大小写；
- 在第 128 字节之后具有标准 `DICM` 文件头的其他文件，例如无扩展名的 DICOM 文件。

扩展名符合规则但内容损坏的文件仍会进入清点，并记录为读取失败。普通非 DICOM 文件会被忽略。

- “成功数”表示 pydicom 成功解析元数据的文件数，即使其中有标签缺失。
- “检查数”“序列数”“唯一实例数”分别按非空 Study、Series、SOP UID 去重。
- 上述基础统计保留“观测到非空UID”的旧口径，可能包含非法UID，不是有效对象认证；新树与series.csv对无效/空UID逐文件后备，因此未归组分支数可能与基础UID去重数不同。
- “重复实例数”表示出现两次及以上的 SOPInstanceUID 组数，不是多余文件数。
- `MissingTags`在V1.1改为当前适用规则下无可用值的关键/建议字段，不再把所有读取字段视为所有对象的必填字段。这会改变部分文件的异常判定；原CSV列顺序不变。
- “需核对文件数”对有问题的文件去重；“问题条数”按结构化问题计数，重复UID组仅计一条，关联文件可有其他问题。三者不能相加或互相替代。
- “已检查”仅表示本版所选字段没有发现问题；“读取成功”只表示解析成功；“未评估”不表示通过。
- 缺失 SOPInstanceUID 的记录不参与唯一实例和重复统计。

## CSV 字段

文件CSV每个候选文件一行，按以下顺序保留19列。编码为带BOM的UTF-8，适合直接使用Excel打开。

| 分组 | 列名（按导出顺序） |
|---|---|
| 路径 | `FilePath` |
| 已导出的DICOM字段 | `Modality`、`StudyInstanceUID`、`SeriesInstanceUID`、`SOPInstanceUID`、`Rows`、`Columns`、`PixelSpacing`、`SliceThickness`、`ImagePositionPatient`、`ImageOrientationPatient`、`InstanceNumber`、`RescaleSlope`、`RescaleIntercept` |
| 读取状态与异常 | `Status`、`AnomalyTypes`、`MissingTags`、`IsDuplicate`、`ErrorMessage` |

文件CSV继续使用`dicom_inventory_时间戳.csv`和原19列，不导出SOPClassUID、FrameOfReferenceUID和ImageType，也不代表全部已读取元数据。新增分类、空间参照、图像类型在GUI/核心可见，不打破旧CSV结构。问题CSV保留V1.1列并追加`Evidence`，证据为JSON对象，UID/文件集合为JSON数组。

`series.csv`每个有效Series UID一行；空/非法UID或读取失败逐文件一行未归组后备。19列包含SeriesKey、SeriesInstanceUID、StudyUIDs、FileCount、Modalities、RowsValues、ColumnsValues、PixelSpacingValues、SliceThicknessValues、OrientationValues、ProjectedRangeMM、SortedPositions、IssueCount、HighestSeverity、OverallStatus、RuleAssessments、Files、FrameOfReferenceUIDs、ImageTypes。集合和规则状态为JSON；无可靠空间范围时为JSON null，不填虚假范围。保留全部不同字符串表示和空值（即使数值在容差内）；不任意选一个值作为统一参数。序列导出亦UTF-8-SIG、防覆盖、独立输出、公式保护。

问题清单首次命名`issues.csv`，已有文件时使用`issues_1.csv`等，文件清单也不覆盖已有文件。写入失败不报告成功，可能留下未完成的导出文件，应检查后重新导出。潜在Excel公式文本以单引号前缀保护，常规数值保持原表示；并非用户输入匿名化服务。元数据解析异常仅保留异常类别和固定提示，不将解析器的原始异常值直接写进日志/CSV。

GUI及核心API都会验证独立输出目录，传入`source_root`可以禁止输出到整个源目录及其后代；旧API未提供根目录时至少禁止写到输入文件所在目录及其后代。建议调用API时明确提供真实数据源根目录。未完成结果导出带partial前缀和匹配批次说明，不能解释为完整数据源的质量报告；CSV本身不新增批次列。

`comparison.csv`新增20列：ComparisonID、BaselineRun、CurrentRun、RecordType、RecordKey、Status、Observation、IssueID、RuleCode、Description、BaselineFiles、CurrentFiles、Before、After、HandlingBefore、HandlingAfter、SystemAction、Reason、Comparable、Limitations。一份文件含比较范围、数量、文件和问题变化四类行；每行保留可比性及限制，问题行区分观察事实、当时人工状态和实际系统动作。UTF-8-SIG、防公式、防覆盖及两批来源目录写入守卫保持。

不可完整比较时文件名为`partial_comparison.csv`，已有文件追加数字；范围/限制已在CSV内，不另生成scan.json。它不等同“已解决/已关闭清单”。文件、序列和问题CSV原结构保持；前三类未完成批次继续配套.scan.json。

## 在其他程序中调用核心逻辑

以下示例包含所需导入。运行前安装项目依赖，将数据源和输出路径替换为本机可访问的目录；输出目录必须独立于数据源。这些路径是示例，不会自动生成演示数据。

```python
from pathlib import Path

from dicom_inventory.core import discover_dicom_files, export_csv, export_issues_csv, export_series_csv, inventory_files

source = Path(r"D:\dicom_source")
files = discover_dicom_files(source)
result = inventory_files(files, source_root=source)
csv_path = export_csv(result, Path(r"D:\inventory_output"))
issues_path = export_issues_csv(result, Path(r"D:\inventory_output"))
series_path = export_series_csv(result, Path(r"D:\inventory_output"))

print(result.summary)
print(csv_path)
```

## 项目结构

```text
DICOMInventory/
├─ dicom_inventory/
│  ├─ core/          # GUI 无关的发现、清点、统计和 CSV 导出
│  ├─ gui/           # PySide6 窗口、表格模型和后台工作线程
│  │                 # comparison_ui/dialogs局部复检，worker_lifecycle安全销毁
│  ├─ storage.py     # GUI无关的SQLite工作库、事务与快照保存/重开
│  ├─ handling.py    # 人工状态、证据轨迹、审计及1→2迁移
│  ├─ handling_export.py # 持久化状态的扩展问题CSV
│  ├─ comparison_store.py # 2→3迁移、对比保存与系统状态审计事务
│  └─ snapshot_codec.py # 固定字段白名单和版本化严格元数据编码
├─ tests/            # 核心测试与 GUI 启动冒烟测试
├─ requirements.txt
├─ requirements-dev.txt
└─ README.md
```
