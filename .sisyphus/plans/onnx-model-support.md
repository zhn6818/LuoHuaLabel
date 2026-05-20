# ONNX 模型预标注支持

## TL;DR

> **Quick Summary**: 为 LuoHuaLabel 添加 ONNX 模型上传与预标注功能。用户加载 YOLOv8/v11 ONNX 模型后，可像 SAM3 一样悬停预览检测框+点击确认，实现自动预标注。
>
> **核心设计**: 全图 YOLO 推理在后台一次性完成→缓存检测结果→悬停时查表定位→点击确认生成标注形状。复用 Canvas 现有形状渲染体系，最小化代码改动。
>
> **Deliverables**:
> - 新增 `core/onnx_client.py` — ONNX 模型加载 + 推理 + 悬停查表
> - 修改 `ui/main_window.py` — 工具栏增加 ONNX 开关和加载按钮
> - 修改 `main.py` — 信号连接、回调处理、互斥逻辑
> - 修改 `core/canvas.py` — ONNX 悬停预览和点击确认
> - 修改 `requirements.txt` — 添加 onnxruntime
>
> **Estimated Effort**: Medium（3-4 小时）
> **Parallel Execution**: YES — 2 waves
> **Critical Path**: onnx_client.py → UI → main.py 集成

---

## Context

### Original Request
用户希望上传 ONNX 模型（YOLOv8/v11）进行类似 SAM3 的预标注：悬停显示预览框，点击确认生成标注。

### Interview Summary
**Key Discussions**:
- **交互模式**: 悬停预览 + 点击确认（类似 SAM 的 hover + click 体验）。YOLO 全图推理一次性完成，悬停通过查表定位，零延迟
- **模型类型**: YOLOv8/v11 ONNX 检测模型（boxes + class_id + confidence）
- **运行后端**: macOS 用 CoreML（onnxruntime 内置），NVIDIA 用 CUDA，通用回退到 CPU
- **与 SAM 关系**: 互斥模式（ONNX 开启时 SAM 关闭，反之亦然）

**Research Findings**:
- 现有 SAM3 管道使用 3 个 QThread（ModelLoadWorker, SetImageWorker, SamInferenceWorker）+ SAMClient QObject 包装器
- Canvas.handle_sam_result 已处理 4 种形状模式，ONNX 只需输出形如 `(list, list, list, float, bool)` 的信号即可复用
- ONNX 运行时在 macOS 上使用 CoreMLExecutionProvider（比 CPU 快 2-3x），在 NVIDIA 上使用 CUDAExecutionProvider
- YOLOv8 ONNX 导出有两种输出格式：原始输出（[1,84,8400]需后处理）和带 NMS 输出（[1,6,N]直接使用）

---

## Work Objectives

### Core Objective
支持用户上传 ONNX 模型并进行高效预标注，与 SAM3 智能标注共存互补。

### Concrete Deliverables
- `core/onnx_client.py` — ONNX 模型管理 + 推理 + 悬停查表
- 工具栏增加「📦 加载 ONNX 模型」按钮 + ONNX 开关
- Canvas 增加 ONNX 悬停预览 + 点击确认逻辑
- 全图预标注 + 单点击确认两种工作流

### Definition of Done
- 加载 ONNX 模型后工具栏显示模型名
- 开启 ONNX 模式后，悬停时鼠标所在位置的检测框显示橙色虚线预览
- 点击确认后生成永久矩形标注框，类别名称从 YOLO class_id 映射
- 全图预标注按钮一键添加所有高置信度检测框
- 切换图片时自动重新运行推理
- 与 SAM 模式互斥切换

### Must Have
- 支持 YOLOv8/v11 ONNX 模型加载和推理
- 悬停预览 + 点击确认交互（同类 SAM 体验）
- 全图预标注一键添加所有检测
- macOS CoreML + NVIDIA CUDA + CPU 回退

### Must NOT Have (Guardrails)
- 不改动 canvas.py/shapes.py 现有形状渲染逻辑（复用 handle_sam_result 的信号接口）
- 不修改主应用现有的标注流程（handle_new_shape 不变）
- 不做自定义 ONNX 模型格式的泛化兼容（只定位 YOLO 检测）
- 不引入 GPU 驱动级别的依赖（onnxruntime 自带 provider 管理）

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: NO（纯 Python 应用，无自动化测试框架）
- **Automated tests**: NO（验证通过 Agent-Executed QA）
- **Framework**: N/A

### QA Policy
所有任务通过 Agent-Executed QA Scenarios 验证，每个场景使用具体工具 + 明确步骤 + 可断言结果。

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (核心模块 - 可并行):
├── Task 1: core/onnx_client.py - ONNX 模型加载器（ONNXModelLoader QThread）
├── Task 2: core/onnx_client.py - ONNX 推理+查表引擎（ONNXClient）
└── Task 3: core/onnx_client.py - YOLO 后处理（解析+NMS+坐标转换）

Wave 2 (UI 集成 - 依赖 Wave 1):
├── Task 4: ui/main_window.py - 工具栏 ONNX 按钮和开关
├── Task 5: canvas.py - ONNX 悬停预览和点击确认
├── Task 6: main.py - 信号连接和回调
└── Task 7: requirements.txt - onnxruntime 依赖

Wave FINAL (验证):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review
└── Task F3: Real QA (Playwright for browser / tmux for CLI / curl for API)
  → Present results → Get explicit user okay
```

### Dependency Matrix

- **1-3**: independent → 4-6, 1
- **4**: 1 → 6, 2
- **5**: 1, 2 → 6, 2
- **6**: 4, 5 → 7, 2
- **7**: 6 → FINAL, 1

---

## TODOs

- [ ] 1. **ONNXModelLoader — 模型加载工作线程**

  **What to do**:
  创建 `ONNXModelLoader(QThread)` 类，负责在后台线程加载 ONNX 模型文件。
  - 读取 .onnx 文件创建 `onnxruntime.InferenceSession`
  - 自动检测并选用最优 ExecutionProvider：macOS → CoreML，NVIDIA → CUDA，通用 → CPU
  - 读取模型输入名称、输入形状（用于后续预处理）
  - 加载成功后尝试从同目录读取 `classes.txt`（每行一个类别名，行号=class_id）
  - 发射 `model_loaded(object, bool, str)` 信号：（session, success, msg）
  - 失败时发射 `model_loaded(None, False, str(e))`

  **Provider 选择逻辑**:
  ```python
  def _get_providers():
      providers = []
      # macOS Apple Silicon: CoreML (最快)
      import platform
      if platform.system() == 'Darwin':
          providers.append('CoreMLExecutionProvider')
      # NVIDIA GPU: CUDA
      providers.append('CUDAExecutionProvider')  # 不可用会静默跳过
      providers.append('CPUExecutionProvider')   # 兜底
      return providers
  ```

  **Must NOT do**:
  - 不要导入 `torch` 或任何 PyTorch 依赖
  - 不要在主线程创建 session

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 需要 ONNX Runtime API 知识 + QThread 线程模型 + 多平台 provider 管理
  - **Skills**: 无需额外技能

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Tasks 4, 5, 6
  - **Blocked By**: None

  **References**:
  - `core/sam_client.py:ModelLoadWorker` — 现有模型加载器 QThread 模式
  - `onnxruntime` 官方文档: https://onnxruntime.ai/docs/api/python/

  **Acceptance Criteria**:
  - [ ] 代码完整：ONNXModelLoader 类存在于 `core/onnx_client.py`
  - [ ] 加载 YOLOv8 导出的 .onnx 文件成功返回非空 session
  - [ ] 加载不存在的文件发射 False 信号
  - [ ] 同目录有 classes.txt 时正确加载类别名

  **QA Scenarios**:
  ```
  Scenario: 正常加载 ONNX 模型
    Tool: Bash
    Preconditions: YOLOv8.onnx 文件和 classes.txt 存在于测试目录
    Steps:
      1. Python：from core.onnx_client import ONNXModelLoader
      2. 创建 ONNXModelLoader 并调用 run 加载 YOLOv8.onnx
    Expected Result: session 不为空，类别名正确从 classes.txt 读取
    Evidence: .sisyphus/evidence/task-1-load.onnx

  Scenario: 加载不存在的文件
    Tool: Bash
    Preconditions: 错误的文件路径
    Steps:
      1. 创建 ONNXModelLoader 用不存在路径调用
    Expected Result: 发射 model_loaded(None, False, error_msg)
    Evidence: .sisyphus/evidence/task-1-load-error.txt
  ```

  **Commit**: NO (groups with Tasks 2, 3)

---

- [ ] 2. **ONNXClient — 客户端包装器 + 悬停查表引擎**

  **What to do**:
  创建 `ONNXClient(QObject)` 类，作为 ONNX 功能的高层 API 封装。
  
  **信号定义**:
  ```python
  class ONNXClient(QObject):
      model_status_changed = Signal(bool, str)     # (success, msg)
      preview_ready = Signal(list, list, list, float)  # ([], [x,y,w,h], [], score) 复用 canvas 格式
      shape_confirmed = Signal(object)             # RectShape 实例
  ```

  **核心逻辑**:
  1. 持有一个 `ONNXModelLoader` 实例用于异步加载模型
  2. 加载成功后创建 `InferenceSession`
  3. `set_image(image_path)` — 异步对图片进行全图 YOLO 推理（使用 QThread/Queue 模式）
     - 读取图片 → 预处理（letterbox resize 到模型输入尺寸）
     - 运行 session.run() → 后处理（解析输出 + 置信度过滤 + NMS）
     - 缓存检测结果 `_detections: [{bbox: [x,y,w,h], confidence: float, class_id: int, class_name: str}]`
     - 发射 preview_ready 通知 UI 状态变更
  4. `find_detection_at(x, y)` — 从缓存检测结果中查找包含该坐标的最高置信度检测
     - 返回 `{bbox, confidence, class_id, class_name}` 或 None
  5. 预处理方法: `_preprocess(image: np.ndarray) → np.ndarray`
     - 用 OpenCV letterbox 缩放到模型输入尺寸
     - 归一化 (除以 255)，添加 batch 维度
     - 返回 float32 张量
  6. 后处理委托给专用方法（见 Task 3）

  **Must NOT do**:
  - 不要在信号里传递复杂对象（只传递基础类型 + 列表）
  - 不要阻塞主线程

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 需要全面理解 SAMClient 架构并精确复现 QThread + Queue 模式，数据流跨线程协调
  - **Skills**: 无需额外技能

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Tasks 4, 5, 6
  - **Blocked By**: None

  **References**:
  - `core/sam_client.py:SAMClient` — 完整的 QObject 客户端模式（信号定义、worker 管理、queue 调度）
  - `core/sam_client.py:SamInferenceWorker` — Queue 调度模式（maxsize=1 + drain + polling loop）
  - `core/sam_client.py:SetImageWorker` — 异步图像处理模式（适合参考 set_image 异步化设计）

  **Acceptance Criteria**:
  - [ ] ONNXClient 类完整实现，包含所有信号和方法
  - [ ] set_image 在后台运行推理，不阻塞主线程
  - [ ] find_detection_at 对包含坐标的检测返回正确结果
  - [ ] find_detection_at 对空白区域返回 None

  **QA Scenarios**:
  ```
  Scenario: ONNXClient 加载模型并推理
    Tool: Bash
    Preconditions: ONNXClient 实例 + YOLOv8.onnx + 测试图片
    Steps:
      1. client.load_model('YOLOv8.onnx')
      2. client.set_image('test.jpg')
      3. 等待 detections 缓存就绪
      4. result = client.find_detection_at(100, 100)
    Expected Result: 如果 (100,100) 在检测框内，返回非空 detection
    Evidence: .sisyphus/evidence/task-2-client.txt
  ```

  **Commit**: NO (groups with Tasks 1, 3)

---

- [ ] 3. **YOLO ONNX 后处理 — 解析 + NMS + 坐标转换**

  **What to do**:
  在 `core/onnx_client.py` 内实现 YOLOv8/v11 ONNX 输出的后处理逻辑。

  **支持的输出格式**:
  ```
  格式 A（带 NMS）: [1, 6, N] — x1,y1,x2,y2,confidence,class_id
  格式 B（原始输出）: [1, 84, 8400] — 4 bbox + 80 class scores
  ```

  **后处理管线**:
  ```python
  def postprocess(outputs, orig_shape, model_input_size, conf_thresh=0.5, iou_thresh=0.45):
      # 1. 检测输出格式
      # 2. 如果是原始输出: sigmoid → 解码 bbox → 置信度过滤 → NMS
      # 3. 如果是 NMS 输出: 直接置信度过滤
      # 4. 坐标缩放: 从模型输入空间映射回原始图像空间
      # 5. 转换为 [x, y, w, h] 格式
      # 6. 返回 detections: [{bbox:[x,y,w,h], confidence, class_id, class_name}]
  ```

  **预处理**: letterbox resize 实现
  ```python
  def letterbox(im, new_shape=(640, 640), color=(114, 114, 114)):
      # OpenCV 实现保持宽高比的 resize + pad
      # 返回 resized image + scale + padding
  ```

  **类别名查找**:
  - 优先使用加载模型时读取的 `classes.txt`
  - 如果没有，使用 `class_{class_id}` 作为默认名

  **Must NOT do**:
  - 不要依赖 numpy 以外的库（除 OpenCV）
  - 不要引入 torch 或 torchvision 的 NMS 实现

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: YOLO 后处理需要精确理解输出张量结构，NMS 算法实现
  - **Skills**: 无需额外技能

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Tasks 5, 6
  - **Blocked By**: None

  **References**:
  - `core/sam_client.py` — 现有后处理（findContours, boundingRect, minAreaRect）在 inference worker 中
  - YOLOv8 仓库: https://github.com/ultralytics/ultralytics (后处理逻辑)
  - YOLO ONNX 输出格式参考: https://docs.ultralytics.com/modes/export/#export-formats

  **Acceptance Criteria**:
  - [ ] letterbox 预处理正确缩放并保持宽高比
  - [ ] 解析 YOLOv8 ONNX 输出为结构化 detection 列表
  - [ ] 置信度过滤和 NMS 正确工作（重叠框被抑制）
  - [ ] 坐标从模型输入空间映射回原始图像空间
  - [ ] 返回结果格式一致：{bbox, confidence, class_id, class_name}

  **QA Scenarios**:
  ```
  Scenario: YOLO 后处理解析
    Tool: Bash
    Preconditions: 模拟 YOLOv8 ONNX 输出 numpy 数组
    Steps:
      1. 创建模拟输出数组（格式 A: [1,6,10]）
      2. 运行 postprocess()
    Expected Result: 返回正确的 detections 列表，NMS 正确抑制重叠框
    Evidence: .sisyphus/evidence/task-3-postprocess.txt

  Scenario: letterbox 预处理
    Tool: Bash
    Preconditions: 1920x1080 测试图片
    Steps:
      1. 运行 letterbox(img, (640, 640))
    Expected Result: 输出 640x640 图片，内容居中，无变形
    Evidence: .sisyphus/evidence/task-3-letterbox.txt
  ```

  **Commit**: NO (groups with Tasks 1, 2)

---

- [ ] 4. **工具栏 UI — ONNX 加载按钮 + ONNX 开关**

  **What to do**:
  在 `ui/main_window.py` 的工具栏 SAM 区域下方增加 ONNX 功能区。

  **UI 元素**:
  1. `actionLoadONNX` — QAction，文字「📦 加载 ONNX 模型」
     - 样式同 btnDatasetTool（Pattern C 暗色工具栏按钮）
  2. 模型状态文字标签（显示当前加载的模型文件名，或「未加载 ONNX 模型」）
  3. `onnxSwitch` — SwitchControl 实例（复用 SAM 开关组件）
  4. `onnxLabel` — QLabel，文字「ONNX 预标注」
  5. `onnxDetectBtn` — QPushButton，文字「⚡ 全图预标注」
     - 仅在 ONNX 模型加载后可用
     - 样式同 samPromptBtn（Pattern A 绿色按钮）

  **布局**（在 SAM 区域后加分隔符后）:
  ```
  self.toolBar.addSeparator()
  self.onnxWidget = QWidget()
  onnxLayout = QVBoxLayout(self.onnxWidget)
  onnxLayout.addWidget(self.actionLoadONNX, as button)
  onnxLayout.addWidget(self.onnxStatusLabel)  # 模型名/状态
  onnxLayout.addWidget(self.onnxDetectBtn)
  onnxLayout.addWidget(self.onnxSwitch, alignment=Qt.AlignCenter)
  onnxLayout.addWidget(self.onnxLabel, alignment=Qt.AlignCenter)
  self.toolBar.addWidget(self.onnxWidget)
  ```

  **交互逻辑**:
  - 点击「📦 加载 ONNX 模型」 → QFileDialog.getOpenFileName filter `.onnx`
  - 模型加载中 → 状态标签显示「正在加载模型...」橙色
  - 模型加载成功 → 状态标签显示模型文件名绿色
  - 模型加载失败 → 状态标签显示错误信息红色
  - ONNX 开关（未加载模型时 disabled）

  **Must NOT do**:
  - 不要改变现有 SAM 区域的布局或样式
  - 不要用 emoji 以外的 Unicode 特殊字符

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: UI 控件创建 + 样式匹配现有暗色主题
  - **Skills**: 无需额外技能

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7)
  - **Blocks**: Task 6
  - **Blocked By**: Task 1

  **References**:
  - `ui/main_window.py:226-384` — 工具栏布局完整模式（QAction + 自定义 QWidget + QPushButton）
  - `ui/main_window.py:SwitchControl` — 可复用的开关组件
  - `ui/style.qss` — 暗色主题颜色常量
  - `ui/main_window.py:FormatSelectorWidget` — 自定义 QWidget 嵌入工具栏模式

  **Acceptance Criteria**:
  - [ ] 加载 ONNX 按钮可点击，弹出文件选择对话框过滤 .onnx 文件
  - [ ] 状态标签正确显示加载状态（加载中/成功/失败）
  - [ ] ONNX 开关在未加载模型时 disabled，加载后 enabled
  - [ ] 全图预标注按钮在模型加载后可用
  - [ ] UI 风格与现有暗色主题一致（颜色、圆角、字体）

  **QA Scenarios**:
  ```
  Scenario: 加载 ONNX 模型按钮
    Tool: Bash (or interactive_bash if UI)
    Preconditions: 应用已启动
    Steps:
      1. 点击「📦 加载 ONNX 模型」按钮
      2. 选择测试 YOLOv8.onnx 文件
    Expected Result: 状态标签显示模型文件名（绿色）
    Evidence: .sisyphus/evidence/task-4-ui.txt

  Scenario: ONNX 开关初始状态
    Tool: Bash
    Preconditions: 未加载 ONNX 模型
    Steps:
      1. 检查 onnxSwitch enabled 状态
    Expected Result: onnxSwitch 为 disabled
    Evidence: .sisyphus/evidence/task-4-switch.txt
  ```

  **Commit**: NO (groups with Tasks 5, 6)

---

- [ ] 5. **Canvas 集成 — ONNX 悬停预览 + 点击确认**

  **What to do**:
  在 `core/canvas.py` 的 `Canvas` 类中增加 ONNX 模式支持。

  **新增属性和方法**:
  ```python
  # 在 __init__ 中增加:
  self.onnx_enabled = False
  self.onnx_client = None
  self.onnx_hover_item = None  # QGraphicsRectItem 预览框
  
  # 新增方法:
  def set_onnx_enabled(self, enabled):
      self.onnx_enabled = enabled
      if not enabled and self.onnx_hover_item:
          self.removeItem(self.onnx_hover_item)
          self.onnx_hover_item = None
  
  def show_onnx_preview(self, detection):
      """显示 ONNX 悬停预览框（橙色虚线 + 类别标签）"""
      if self.onnx_hover_item:
          self.removeItem(self.onnx_hover_item)
      x, y, w, h = detection['bbox']
      rect = QRectF(x, y, w, h)
      item = QGraphicsRectItem(rect)
      item.setPen(QPen(QColor(255, 165, 0), 2, Qt.DashLine))  # 橙色 = ONNX
      item.setBrush(QBrush(QColor(255, 165, 0, 50)))
      # 添加类别标签文字
      label_text = QGraphicsTextItem(f"{detection['class_name']} ({detection['confidence']:.2f})", item)
      label_text.setDefaultTextColor(QColor(255, 165, 0))
      self.addItem(item)
      self.onnx_hover_item = item
  
  def hide_onnx_preview(self):
      if self.onnx_hover_item:
          self.removeItem(self.onnx_hover_item)
          self.onnx_hover_item = None
  ```

  **mouseMoveEvent 修改**:
  在现有代码中增加 ONNX 分支（在 CAM 和 SAM 之前检查）:
  ```python
  # ---- ONNX 悬停预览 ----
  if self.onnx_enabled and self.onnx_client and self.is_inside_image(pt):
      detection = self.onnx_client.find_detection_at(clamped_pt.x(), clamped_pt.y())
      if detection:
          self.show_onnx_preview(detection)
      else:
          self.hide_onnx_preview()
      return
  ```

  **mousePressEvent 修改**:
  在左键点击中增加 ONNX 确认分支:
  ```python
  # ---- ONNX 点击确认 ----
  if self.onnx_enabled and self.onnx_client and event.button() == Qt.LeftButton and self.is_inside_image(pt):
      detection = self.onnx_client.find_detection_at(clamped_pt.x(), clamped_pt.y())
      if detection:
          # 创建永久 RectShape 并设置 label
          from core.shapes import RectShape
          x, y, w, h = detection['bbox']
          shape = RectShape(QRectF(x, y, w, h))
          shape.label = detection['class_name']
          # 更新标签显示
          if hasattr(shape, 'update_label_text'):
              shape.update_label_text(detection['class_name'])
          if hasattr(shape, 'update_label_position'):
              shape.update_label_position(shape)
          self.shape_drawn.emit(shape)
          self.hide_onnx_preview()
      return
  ```

  **cancel_drawing 修改**:
  清理 onnx_hover_item:
  ```python
  if self.onnx_hover_item:
      self.removeItem(self.onnx_hover_item)
      self.onnx_hover_item = None
  ```

  **Must NOT do**:
  - 不要修改 SAM 相关的鼠标事件路径（保持向后兼容）
  - 不要改变现有形状的视觉样式（ONNX 用橙色以示区分）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 需要精确的鼠标事件路由编排，避免与 SAM 模式冲突
  - **Skills**: 无需额外技能

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 4, 6, 7)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 6
  - **Blocked By**: Tasks 1, 2, 3

  **References**:
  - `core/canvas.py:23-70` — Canvas.__init__ 所有属性和辅助元素
  - `core/canvas.py:119-136` — mouseMoveEvent SAM 悬停分支
  - `core/canvas.py:347-366` — mousePressEvent SAM 左键确认分支
  - `core/canvas.py:485-496` — cancel_drawing 清理逻辑
  - `core/canvas.py:159-243` — handle_sam_result 预览/确认渲染模式

  **Acceptance Criteria**:
  - [ ] ONNX 模式开启时，悬停覆盖检测框显示橙色虚线预览
  - [ ] 点击确认生成带类别名的 RectShape
  - [ ] ONNX 关闭/切换模式时清除预览
  - [ ] 与 SAM 模式互不干扰

  **QA Scenarios**:
  ```
  Scenario: ONNX 悬停预览
    Tool: Bash (script simulating mouse events)
    Preconditions: ONNXClient 有缓存检测, onnx_enabled=True
    Steps:
      1. 模拟 mouseMoveEvent 到检测框坐标
    Expected Result: onnx_hover_item 显示橙色虚线矩形
    Evidence: .sisyphus/evidence/task-5-hover.txt

  Scenario: ONNX 点击确认
    Tool: Bash
    Preconditions: ONNX 悬停预览激活
    Steps:
      1. 模拟 mousePressEvent 左键（在检测框内）
    Expected Result: shape_drawn 信号发射，包含正确类名的 RectShape
    Evidence: .sisyphus/evidence/task-5-click.txt
  ```

  **Commit**: NO (groups with Tasks 4, 6)

---

- [ ] 6. **MainWindow 集成 — 信号连接 + 回调 + 互斥**

  **What to do**:
  在 `main.py` 中集成 ONNX 客户端的完整生命周期。

  **__init__ 中增加**:
  ```python
  self.onnx_client = ONNXClient(self)
  self.onnx_client.model_status_changed.connect(self._on_onnx_model_status)
  self.scene.onnx_client = self.onnx_client
  ```

  **_connect_signals 中增加**:
  ```python
  self.actionLoadONNX.triggered.connect(self.load_onnx_model)
  self.onnxSwitch.toggled.connect(self.on_onnx_toggled)
  self.onnxDetectBtn.clicked.connect(self.run_onnx_full_detect)
  self.listFiles.currentItemChanged.connect(self._onnx_on_file_changed)
  ```

  **新增方法**:
  ```python
  def load_onnx_model(self):
      """选择并加载 ONNX 模型文件"""
      filepath, _ = QFileDialog.getOpenFileName(self, "选择 ONNX 模型文件", "", "ONNX 模型 (*.onnx)")
      if filepath:
          self.helpLabel.setText("正在加载 ONNX 模型...")
          self.helpLabel.setStyleSheet("color: orange;")
          self.onnx_client.load_model(filepath)

  def _on_onnx_model_status(self, success, msg):
      """ONNX 模型加载状态回调"""
      if success:
          self.helpLabel.setText(f"ONNX 模型就绪: {os.path.basename(msg)}")
          self.helpLabel.setStyleSheet("color: green;")
          # 如果有当前图片，自动运行推理
          if self.current_image_path:
              self.onnx_client.set_image(self.current_image_path)
      else:
          self.helpLabel.setText(f"ONNX 模型加载失败: {msg}")
          self.helpLabel.setStyleSheet("color: red;")

  def on_onnx_toggled(self, checked):
      """ONNX 开关切换"""
      self.scene.set_onnx_enabled(checked)
      if checked and self.samSwitch.isChecked():
          self.samSwitch.setChecked(False)  # 互斥

  def run_onnx_full_detect(self):
      """全图预标注：添加所有高置信度检测框"""
      if not self.current_image_path or not self.onnx_client:
          return
      self.helpLabel.setText("正在全图预标注...")
      self.helpLabel.setStyleSheet("color: orange;")
      # 触发 onnx 推理，完成后自动添加检测结果
      self.onnx_client.set_image(self.current_image_path)

  def _onnx_on_file_changed(self, current, previous):
      """切换图片时自动更新 ONNX 缓存"""
      if current and self.onnx_client and self.onnx_client.is_loaded():
          self.onnx_client.set_image(current.text())
  ```

  **update_model_status 修改**:
  确保模型加载完成后，如果 ONNX 已启用则自动推理：
  ```python
  # 在模型加载成功后的分支中增加:
  if self.onnx_client and self.onnx_client.is_loaded() and self.current_image_path:
      self.onnx_client.set_image(self.current_image_path)
  ```

  **Must NOT do**:
  - 不要修改现有的 SAM 信号连接
  - 不要引入 onnx 模块（使用 ONNXClient 封装）

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 需要理解整个信号/槽体系，协调 SAM 与 ONNX 的互斥逻辑，图片切换生命周期
  - **Skills**: 无需额外技能

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 4, 5, 7)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 7
  - **Blocked By**: Tasks 4, 5, 1, 2, 3

  **References**:
  - `main.py:19-66` — __init__ 实例化和信号连接模式
  - `main.py:68-100` — _connect_signals 信号路由
  - `main.py:549-562` — update_model_status 模型状态回调模式
  - `main.py:564-587` — on_file_selected 图片切换生命周期
  - `main.py:338-345` — on_sam_toggled SAM 开关互斥

  **Acceptance Criteria**:
  - [ ] 加载 ONNX 模型后自动对当前图片运行推理
  - [ ] ONNX 开关与 SAM 开关互斥
  - [ ] 切换图片时自动更新 ONNX 检测缓存
  - [ ] 全图预标注按钮正确触发推理并添加检测框
  - [ ] 状态栏正确显示 ONNX 状态

  **QA Scenarios**:
  ```
  Scenario: ONNX 加载 + 自动推理
    Tool: Bash
    Preconditions: SAM 模型已加载，当前有图片
    Steps:
      1. 模拟 load_onnx_model（调用 onnx_client.load_model）
      2. 等待 model_loaded 信号
    Expected Result: 自动调用 set_image 对当前图片推理
    Evidence: .sisyphus/evidence/task-6-auto-detect.txt

  Scenario: ONNX 与 SAM 互斥
    Tool: Bash
    Preconditions: SAM 已开启
    Steps:
      1. 调用 on_onnx_toggled(True)
    Expected Result: samSwitch.setChecked(False) 被调用
    Evidence: .sisyphus/evidence/task-6-mutex.txt
  ```

  **Commit**: YES
  - Message: `feat(onnx): add YOLO ONNX model pre-annotation support`
  - Files: `core/onnx_client.py`, `ui/main_window.py`, `core/canvas.py`, `main.py`, `requirements.txt`

---

- [ ] 7. **更新依赖 — onnxruntime**

  **What to do**:
  在 `requirements.txt` 中添加 onnxruntime 依赖。

  ```txt
  # ONNX 运行时（用于 YOLOv8 预标注）
  onnxruntime>=1.17.0
  ```

  **Must NOT do**:
  - 不要添加 `onnxruntime-gpu`（让 onnxruntime 自动管理 provider，通过代码动态选择 CUDA/CoreML）
  - 不要添加不必要的版本锁定

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 单行修改，无需额外技能

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5, 6)
  - **Blocks**: FINAL
  - **Blocked By**: None

  **References**:
  - `requirements.txt` — 现有依赖列表

  **Acceptance Criteria**:
  - [ ] requirements.txt 包含 onnxruntime>=1.17.0
  - [ ] pip install -r requirements.txt 成功

  **QA Scenarios**:
  ```
  Scenario: 安装依赖
    Tool: Bash
    Preconditions: Python 环境
    Steps:
      1. pip install onnxruntime>=1.17.0
    Expected Result: 安装成功
    Evidence: .sisyphus/evidence/task-7-install.txt
  ```

  **Commit**: NO (groups with Task 6)

---

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists. For each "Must NOT Have": search codebase for forbidden patterns.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `python -m py_compile` on all changed files. Check for: unused imports, missing error handling, thread safety issues, Signal/slot compatibility.
  Output: `Build [PASS/FAIL] | Issues [N] | VERDICT`

- [ ] F3. **Manual QA** — `unspecified-high`
  1. Start app and load YOLOv8.onnx → status shows model name
  2. Open image → ONNX auto-detects objects
  3. Hover over object → orange preview appears
  4. Click → RectShape created with class label
  5. Toggle ONNX off → preview disappears, no interference
  6. Toggle SAM on → ONNX off, SAM works normally
  Output: `Scenarios [N/N pass] | VERDICT`

---

## Commit Strategy

- **1-3**: `core/onnx_client.py` 完整创建（no commit，随 Task 6 一起）
- **4**: `ui/main_window.py` ONNX UI（no commit）
- **5**: `core/canvas.py` ONNX 集成（no commit）
- **6**: `main.py` + `requirements.txt`（commit with 7）
- **7**: 最终一次性提交：
  ```
  feat(onnx): add YOLO ONNX model pre-annotation support
  
  - core/onnx_client.py: ONNX model loader, inference engine, hover lookup
  - ui/main_window.py: toolbar buttons for load/toggle/full-detect
  - core/canvas.py: ONNX hover preview (orange) + click confirm
  - main.py: signal wiring, mutex with SAM, auto-detect on image switch
  - requirements.txt: onnxruntime>=1.17.0
  
  Test: pip install onnxruntime, load YOLOv8.onnx, hover+click verify
  ```

---

## Success Criteria

### Verification Commands
```bash
python -c "from core.onnx_client import ONNXModelLoader, ONNXClient; print('Import OK')"
python -c "import onnxruntime; print(f'ONNX Runtime v{onnxruntime.__version__}'); providers = onnxruntime.get_available_providers(); print(f'Providers: {providers}')"
```

### Final Checklist
- [ ] core/onnx_client.py 创建（ONNXModelLoader + ONNXClient + YOLO 后处理）
- [ ] ui/main_window.py 工具栏 ONNX 区域（加载按钮 + 开关 + 全图预标注）
- [ ] core/canvas.py ONNX 悬停预览和点击确认
- [ ] main.py 信号连接和互斥逻辑
- [ ] requirements.txt 更新
- [ ] onnxruntime 可导入
- [ ] 加载 ONNX 模型后悬停预览正常
- [ ] 点击确认生成正确类别标注
- [ ] 与 SAM 模式互斥切换正常
- [ ] 全图预标注一键添加所有检测框
