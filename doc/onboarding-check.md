# 初次安装配置检查报告

## 📊 检查结果总结

| 检查项 | 状态 | 详情 |
|---|---|---|
| CLI 引导向导 | ✅ **存在** | `agent/cli/onboard.py` - 首次启动时自动触发 |
| 后端 API 配置路由 | ✅ **存在** | `agent/src/api/settings_routes.py` - 支持查询和更新 |
| 前端设置页面 | ✅ **存在** | `frontend/src/pages/Settings.tsx` - 完整的 UI 配置界面 |
| 健康检查端点 | ✅ **存在** | `/health` 路由 - 用于监控后端状态 |
| **首次启动检测** | ❌ **缺失** | 没有机制检测系统是否已初始化 |
| **强制引导工作流** | ❌ **缺失** | 首次启动时不强制配置 API key |
| **前端 Onboarding UI** | ❌ **缺失** | 没有对应 CLI 的前端引导界面 |
| **初始化状态路由** | ❌ **缺失** | 没有 `/api/init/status` 类型的路由 |

---

## ✅ 已实现部分详解

### 1. CLI Onboarding（`agent/cli/onboard.py`）

**现状：** 完整实现，自动触发

```python
def run_onboarding(*, console: Console | None = None) -> Path | None:
    """首次启动自动引导：
    - 检查 ~/.vibe-trading/.env 是否存在
    - 若不存在，启动交互式设置向导
    - 支持：提供商选择 → 模型选择 → API key 输入 → 超时配置
    - 原子文件写入：使用 .env.partial + rename 保证原子性
    """
```

**触发条件：**
```bash
vibe-trading              # CLI 首次运行时检查 ~/.vibe-trading/.env
vibe-trading serve        # 若 .env 不存在，会提示需要配置
```

**支持的提供商：**
- OpenRouter（推荐，支持 200+ 模型）
- OpenAI
- DeepSeek
- Ollama（本地，无需 API key）

**特点：**
- ✅ 支持返回上一步（Esc / ← 键）
- ✅ API key 输入为 masked（隐藏输入）
- ✅ 原子文件写入（崩溃安全）
- ✅ 支持可选的 Tushare token 配置

---

### 2. 后端 API 配置路由（`agent/src/api/settings_routes.py`）

**可用路由：**

```python
# 查询当前 LLM 设置
GET /api/settings/llm
Response: {
    "api_key_configured": bool,      # ← 关键字段：是否已配置
    "provider": str,                  # openai / deepseek / ollama / openrouter
    "model_name": str,
    "base_url": str,
    "api_key_env": str | null,        # 环境变量名
    "providers": [...]                # 支持的提供商列表
}

# 更新 LLM 设置
POST /api/settings/llm
Request: {
    "provider": str,
    "model_name": str,
    "api_key": str,
    "clear_api_key": bool,
    "temperature": float,
    ...
}
```

**检测 API 密钥配置的方法：**
```python
api_key_configured = host._is_configured_secret(api_key, LLM_API_KEY_PLACEHOLDERS)
# 检查 API key 是否在占位符列表中：
# {"", "sk-or-v1-your-key-here", "sk-xxx", "xxx", "gsk_xxx"}
```

---

### 3. 前端设置页面（`frontend/src/pages/Settings.tsx`）

**现状：** 完整的 LLM 配置 UI

```typescript
export function Settings() {
  const [apiKey, setApiKey] = useState("");        // API key 输入
  const [localApiKey, setLocalApiKeyState] = useState(() => getApiAuthKey());

  // 加载当前设置
  api.getLLMSettings()  // GET /api/settings/llm
  
  // 保存更新
  api.updateLLMSettings({ provider, model_name, api_key, ... })  // POST /api/settings/llm
}
```

**功能：**
- ✅ 显示所有支持的 LLM 提供商
- ✅ 动态模型选择
- ✅ API key 配置（输入框）
- ✅ 清除 API key 选项
- ✅ 基础 URL 自定义
- ✅ 高级参数（temperature、timeout、max_retries）

---

## ❌ 缺失部分与改进方案

### 1. 首次启动检测机制

**问题：** 
- 前端无法判断系统是否已初始化
- 即使 API key 未配置，用户也可访问所有页面
- 不强制首次启动的用户进行配置

**改进方案：**
```typescript
// 创建新路由：GET /api/init/status
// 返回初始化状态
{
  "initialized": bool,
  "api_key_configured": bool,
  "recommended_action": "setup" | "ready" | "reconfigure"
}

// 在应用入口（Layout.tsx）检查
useEffect(() => {
  api.getInitStatus().then(status => {
    if (!status.initialized && !status.api_key_configured) {
      navigate('/setup');  // 重定向到设置
    }
  });
}, []);
```

---

### 2. 强制首次启动引导工作流

**问题：**
- 首次启动时无强制配置流程
- 用户可能跳过配置步骤

**改进方案：**

**后端新增路由：**
```python
@app.get("/api/init/status")
async def get_init_status():
    """检查系统初始化状态"""
    settings = await get_llm_settings()
    return {
        "initialized": settings.api_key_configured,
        "api_key_configured": settings.api_key_configured,
        "provider": settings.provider,
        "requires_setup": not settings.api_key_configured,
    }

@app.post("/api/init/complete")
async def mark_init_complete():
    """标记初始化完成（通过验证 API 连接）"""
    # 验证配置的 API 密钥是否有效
    try:
        result = await test_llm_connection()
        return {"status": "initialized", "verified": True}
    except Exception as e:
        return {"status": "error", "verified": False, "detail": str(e)}
```

---

### 3. 前端 Onboarding 组件

**问题：**
- 没有对应 CLI 的前端引导 UI
- 设置界面复杂，不适合首次用户

**改进方案：**

创建 `frontend/src/pages/Onboard.tsx`：
```typescript
export function Onboard() {
  // 步骤流程
  // 1. 欢迎屏幕 + 说明
  // 2. 提供商选择（简化显示，仅展示推荐的 4 个）
  // 3. 模型选择（基于所选提供商显示建议模型列表）
  // 4. API key 输入（masked）
  // 5. 连接测试
  // 6. 完成
  
  const steps = [
    'welcome',
    'provider',
    'model',
    'api_key',
    'test_connection',
    'complete'
  ];
  
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-900 to-slate-800">
      {/* 进度条 */}
      <ProgressBar current={currentStep} total={steps.length} />
      
      {/* 步骤内容 */}
      {currentStep === 'welcome' && <WelcomeStep />}
      {currentStep === 'provider' && <ProviderStep />}
      ...
    </div>
  );
}
```

**路由配置：**
```typescript
{ path: "/setup", element: wrap(Onboard) }  // 新增
```

---

### 4. 前端初始化检查

**改进方案：**

更新 `frontend/src/components/layout/Layout.tsx`：
```typescript
export function Layout() {
  const navigate = useNavigate();
  const [initialized, setInitialized] = useState(null);

  useEffect(() => {
    // 首次加载时检查初始化状态
    api.getInitStatus()
      .then(status => {
        if (!status.api_key_configured) {
          navigate('/setup');  // 重定向到 onboarding
        }
        setInitialized(true);
      })
      .catch(() => setInitialized(true));  // 若查询失败，允许继续
  }, [navigate]);

  if (initialized === null) {
    return <LoadingScreen />;  // 初始化检查中
  }

  return <>{/* 正常布局 */}</>;
}
```

---

## 📋 实现建议（优先级排序）

### 🔴 P1 - 必需（影响首次用户体验）

1. **添加 `/api/init/status` 路由**
   - 文件：`agent/src/api/system_routes.py`
   - 功能：检查系统是否已初始化
   - 返回：`{ initialized: bool, api_key_configured: bool }`

2. **创建前端 Onboard 页面**
   - 文件：`frontend/src/pages/Onboard.tsx`
   - 功能：步骤式 API key 配置向导
   - 路由：`/setup`

3. **在 Layout 中添加初始化检查**
   - 文件：`frontend/src/components/layout/Layout.tsx`
   - 功能：首次加载时检查，未初始化则重定向到 `/setup`

### 🟡 P2 - 推荐（改善体验）

4. **完善 Onboard 流程**
   - 连接测试步骤（验证 API key 有效）
   - 备选提供商列表
   - 错误恢复建议

5. **添加初始化完成标记**
   - 后端路由：`POST /api/init/complete`
   - 验证 LLM 连接是否有效

### 🟢 P3 - 可选（后续优化）

6. **移动端 Onboard 优化**
   - 响应式设计
   - 触屏友好的步骤导航

7. **多语言 Onboard**
   - 使用 i18n 国际化

---

## 🔍 快速集成步骤

### Step 1：后端（5 分钟）
```bash
# 在 agent/src/api/system_routes.py 添加
@app.get("/api/init/status")
async def get_init_status():
    settings = await api.getLLMSettings()
    return {
        "initialized": settings.api_key_configured,
        "api_key_configured": settings.api_key_configured,
    }
```

### Step 2：前端路由（2 分钟）
```typescript
// frontend/src/router.tsx 添加
const Onboard = lazy(() => import("@/pages/Onboard"));
{ path: "/setup", element: wrap(Onboard) }
```

### Step 3：Layout 检查（3 分钟）
```typescript
// frontend/src/components/layout/Layout.tsx 添加
useEffect(() => {
  api.getInitStatus().then(status => {
    if (!status.api_key_configured) navigate('/setup');
  });
}, []);
```

### Step 4：Onboard 页面（15-20 分钟）
创建简化的 6 步流程，复用 Settings 中的逻辑

---

## 总结

| 检查项 | 现状 | 所需工作 |
|---|---|---|
| CLI 首次配置 | ✅ 完整 | — |
| 后端 API 路由 | ✅ 完整 | 添加 `/api/init/status` |
| 前端设置页面 | ✅ 完整 | — |
| 首次启动检测 | ❌ 缺失 | 5-10 分钟实现 |
| 强制引导流程 | ❌ 缺失 | 15-20 分钟实现 |
| 前端 Onboard UI | ❌ 缺失 | 15-20 分钟实现 |

**预计总工作量：30-40 分钟**

