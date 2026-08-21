<script setup lang="ts">
import {ref, onMounted, nextTick, computed} from 'vue'
import axios from 'axios'
import {marked} from 'marked'

// Types
interface Message {
  role: 'user' | 'ai' | 'system'
  content: string
  logs?: LogItem[]
  files?: FileItem[]
  timestamp?: number
  approvalRequests?: any[] // 保存需要审批的请求
  isApproved?: boolean     // 标记是否已经审批过
}

interface LogItem {
  type: string
  title: string
  details: any
  timestamp: string
}

interface FileItem {
  name: string
  path: string
  url: string
}

// State
const inputQuery = ref('')
const messages = ref<Message[]>([])
const status = ref<'idle' | 'running'>('idle')
const socket = ref<WebSocket | null>(null)
const currentSessionPath = ref('')
const currentSessionUrl = ref('')
const messagesEndRef = ref<HTMLElement | null>(null)
const isWelcomeScreen = computed(() => messages.value.length === 0)
const isSidebarOpen = ref(false)
const fileList = ref<any[]>([])
// 生成一个持久的会话ID，如果页面不刷新，ID不变
const currentThreadId = ref(crypto.randomUUID())

// Helper: Scroll to bottom
const scrollToBottom = async () => {
  await nextTick()
  if (messagesEndRef.value) {
    messagesEndRef.value.scrollIntoView({behavior: 'smooth'})
  }
}

// To_do 列表的状态
interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
}

const activeTodos = ref<TodoItem[]>([])
// 控制 To_do 面板是否显示
const isTodoPanelOpen = ref(false)

// Fetch Files
const fetchFiles = async () => {
  if (!currentSessionPath.value) return
  try {
    const res = await axios.get('http://localhost:8000/api/files', {
      params: {path: currentSessionPath.value}
    })
    if (res.data.files) {
      fileList.value = res.data.files.map((f: any) => ({
        ...f,
        // 使用新的下载 API，传入绝对路径
        url: `http://localhost:8000/api/download?path=${encodeURIComponent(f.path)}`
      }))
    }
  } catch (e) {
    console.error('Failed to fetch files', e)
  }
}

// WebSocket Connection
const connectWebSocket = () => {
  const ws = new WebSocket(`ws://localhost:8000/ws/${currentThreadId.value}`)

  ws.onopen = () => {
    console.log('WebSocket Connected')
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      handleSocketMessage(data)
    } catch (e) {
      console.error('Error parsing WS message:', e)
    }
  }

  ws.onclose = () => {
    console.log('WebSocket Disconnected, retrying in 3s...')
    setTimeout(connectWebSocket, 3000)
  }

  socket.value = ws
}

// 发送审批结果的方法
const sendApproval = async (msgIndex: number, actionName: string, isApproved: boolean) => {
  const msg = messages.value[msgIndex];
  msg.isApproved = true; // 隐藏审批按钮
  status.value = 'running'; // 重新转起菊花

  // 动态映射决策类型
  const decisions = msg.approvalRequests!.map(req => {
    if (isApproved) {
      // 用户同意：直接放行
      return { type: 'approve' };
    } else {
     // 用户拒绝：使用 reject 类型，并附带明确的 message 拒绝理由
      return {
        type: 'reject',
        // 这个 message 会直接作为“工具调用失败的反馈”喂给大模型
        message: '人工审批已拒绝该操作，由于涉及敏感数据或高危指令，请停止尝试，并直接向用户解释原因。'
      };
    }
  });

  try {
    await axios.post('http://127.0.0.1:8000/api/approve', {
      thread_id: currentThreadId.value,
      decisions: decisions
    });
  } catch (error) {
    console.error('Approval failed:', error);
  }
}

// Handle Incoming Messages
const handleSocketMessage = (data: any) => {
  const {type, event, message, data: eventData} = data

  if (type === 'pong') return

  let lastAiMsg = messages.value.slice().reverse().find(m => m.role === 'ai')

  if (event === 'session_created') {
    currentSessionPath.value = eventData.path
    const parts = eventData.path.split(/output[\\/]/)
    if (parts.length > 1) {
      currentSessionUrl.value = `http://localhost:8000/outputs/${parts[1].replace(/\\/g, '/')}`
    }
    isSidebarOpen.value = true
    fetchFiles()
  } else if (event === 'hitl_require_approval') {
    if (lastAiMsg) {
      lastAiMsg.approvalRequests = eventData.action_requests;
      // 重置审批状态，确保按钮重新显示出来！
      lastAiMsg.isApproved = false;
      status.value = 'idle'; // 停止动画，等待用户操作
    }
  }
  // 处理主 Agent 的普通工具调用 (画图、生成PDF等)
  else if (event === 'main_tool_start') {
    if (lastAiMsg) {
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'main_tool',
        title: `主智能体执行工具: ${eventData.tool_name}`,
        details: eventData.args,
        timestamp: new Date().toLocaleTimeString()
      })
    }
  }
  // 👇处理 To_do 任务面板更新
  else if (event === 'todos_updated') {
    activeTodos.value = eventData.todos
    isTodoPanelOpen.value = true // 收到 todo 自动打开面板

    if (lastAiMsg) {
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'info',
        title: `更新了任务规划 (共 ${eventData.todos.length} 项)`,
        details: null,
        timestamp: new Date().toLocaleTimeString()
      })
    }
  } else if (event === 'tool_start') {
    // 触发文件列表刷新，以确保用户能看到生成的文件
    if (currentSessionPath.value) {
      // 延迟一点刷新，因为工具刚开始运行，文件可能还没生成
      // 但如果是“写入文件”类工具，可能很快就有了
      // 这里可以尝试立即刷新 + 延迟刷新
      fetchFiles()
      setTimeout(fetchFiles, 2000)
    }

    if (lastAiMsg) {
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'tool',
        title: `使用的工具： ${eventData.tool_name}...`,
        details: eventData.args,
        timestamp: new Date().toLocaleTimeString()
      })

      if (eventData.args && eventData.args.filename && currentSessionUrl.value) {
        if (!lastAiMsg.files) lastAiMsg.files = []
        const fileUrl = `${currentSessionUrl.value}/${eventData.args.filename}`
        // Avoid duplicates
        if (!lastAiMsg.files.find(f => f.name === eventData.args.filename)) {
          lastAiMsg.files.push({
            name: eventData.args.filename,
            path: eventData.args.filename,
            url: fileUrl
          })
        }
      }
    }
  } else if (event === 'assistant_call') {
    // 同样刷新文件列表
    if (currentSessionPath.value) {
      fetchFiles()
    }
    if (lastAiMsg) {
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'agent',
        title: `正在使用助手： ${eventData.assistant_name}...`,
        details: eventData.args,
        timestamp: new Date().toLocaleTimeString()
      })
    }
  } else if (event === 'task_result') {
    if (lastAiMsg) {
      lastAiMsg.content = eventData.result
    } else {
      messages.value.push({
        role: 'ai',
        content: eventData.result,
        timestamp: Date.now()
      })
    }
    status.value = 'idle'
    fetchFiles()
  } else if (event === 'error') {
    messages.value.push({
      role: 'system',
      content: `Error: ${message}`,
      timestamp: Date.now()
    })
    status.value = 'idle'
  }

  scrollToBottom()
}

// Send Message
const sendMessage = async () => {
  if ((!inputQuery.value.trim() && selectedFiles.value.length === 0) || status.value === 'running') return

  const query = inputQuery.value
  inputQuery.value = ''
  status.value = 'running'

  messages.value.push({
    role: 'user',
    content: query,
    timestamp: Date.now()
  })

  messages.value.push({
    role: 'ai',
    content: '', // Start empty, show "Thinking" via logs/status if needed, or placeholder
    logs: [],
    files: [],
    timestamp: Date.now()
  })

  scrollToBottom()

  // Handle File Upload
  if (selectedFiles.value.length > 0) {
    console.log('Uploading files:', selectedFiles.value)

    // Log to UI
    const lastAiMsg = messages.value[messages.value.length - 1]
    if (lastAiMsg && lastAiMsg.role === 'ai') {
      if (!lastAiMsg.logs) lastAiMsg.logs = []

      const fileDetails = selectedFiles.value.map(f => ({name: f.name, size: f.size}))

      lastAiMsg.logs.push({
        type: 'info',
        title: `Uploading ${selectedFiles.value.length} file(s)...`,
        details: fileDetails,
        timestamp: new Date().toLocaleTimeString()
      })
    }

    // Actual Upload
    try {
      const formData = new FormData()
      // Ensure thread_id is available
      if (typeof currentThreadId !== 'undefined' && currentThreadId.value) {
        formData.append('thread_id', currentThreadId.value)
      } else {
        // Fallback if no thread ID (should ideally not happen as initialized in state)
        console.warn('No thread ID found for upload')
      }

      selectedFiles.value.forEach(file => {
        console.log(`Appending file to FormData: name=${file.name}, size=${file.size}, type=${file.type}`)
        formData.append('files', file)
      })

      await axios.post('http://127.0.0.1:8000/api/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      })

      // Clear files after successful upload
      selectedFiles.value = []

      if (lastAiMsg && lastAiMsg.logs) {
        lastAiMsg.logs.push({
          type: 'success',
          title: 'Files uploaded successfully',
          details: null,
          timestamp: new Date().toLocaleTimeString()
        })
      }

    } catch (e: any) {
      console.error('Upload failed', e)
      if (lastAiMsg && lastAiMsg.logs) {
        lastAiMsg.logs.push({
          type: 'error',
          title: 'File upload failed',
          details: e.message || 'Unknown error',
          timestamp: new Date().toLocaleTimeString()
        })
      }
      // Don't stop task execution, but maybe warn user?
    }
  }

  try {
    const payload: any = {query}
    // Only add thread_id if it exists and is not empty
    if (typeof currentThreadId !== 'undefined' && currentThreadId.value) {
      payload.thread_id = currentThreadId.value
    }
    console.log('Sending request payload:', payload)
    const res = await axios.post('http://127.0.0.1:8000/api/task', payload)

    if (res.data && res.data.thread_id) {
      currentThreadId.value = res.data.thread_id
    }
  } catch (error: any) {
    console.error('Request failed:', error)
    let errorMsg = 'Failed to send request.'
    if (error.message) errorMsg += ` (${error.message})`
    if (error.response && error.response.data) {
      errorMsg += ` Server says: ${JSON.stringify(error.response.data)}`
    }

    messages.value.push({
      role: 'system',
      content: errorMsg,
      timestamp: Date.now()
    })
    status.value = 'idle'
  }
}

// File Upload
const fileInputRef = ref<HTMLInputElement | null>(null)
const selectedFiles = ref<File[]>([])

const triggerFileUpload = () => {
  fileInputRef.value?.click()
}

const handleFileChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    // Append new files to existing list
    selectedFiles.value = [...selectedFiles.value, ...Array.from(target.files)]
    console.log('Files selected:', selectedFiles.value)
    // Reset input so same file can be selected again if needed
    target.value = ''
  }
}

const removeFile = (index: number) => {
  selectedFiles.value.splice(index, 1)
}

const renderMarkdown = (text: string) => {
  if (!text) return '<span class="typing-indicator">Thinking...</span>'
  return marked(text)
}

onMounted(() => {
  connectWebSocket()
})
</script>

<template>
  <div class="app-container">
    <!-- Main Content -->
    <main class="main-content" :class="{ 'centered-layout': isWelcomeScreen }">

      <!-- Sidebar Toggle Button -->
      <button
          v-if="currentSessionPath && !isSidebarOpen"
          class="sidebar-toggle-btn"
          @click="isSidebarOpen = true"
          title="Open File Sidebar"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M4 6H20M4 12H20M4 18H20" stroke="currentColor" stroke-width="2" stroke-linecap="round"
                stroke-linejoin="round"/>
        </svg>
      </button>

      <!-- Welcome Screen -->
      <div v-if="isWelcomeScreen" class="welcome-screen">
        <div class="welcome-text">
          <h1>Hello, User</h1>
          <h2>How can I help you today?</h2>
        </div>
      </div>


      <!-- Chat Area -->
      <div v-else class="chat-scroll-area">
        <div class="chat-container">
          <div v-for="(msg, index) in messages" :key="index" class="message-wrapper" :class="msg.role">

            <!-- User Message -->
            <div v-if="msg.role === 'user'" class="message-user">
              <div class="msg-content">{{ msg.content }}</div>
            </div>

            <!-- AI Message -->
            <div v-else-if="msg.role === 'ai'" class="message-ai">
              <div class="ai-avatar">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 2L14.5 9.5L22 12L14.5 14.5L12 22L9.5 14.5L2 12L9.5 9.5L12 2Z" fill="url(#grad1)"/>
                  <defs>
                    <linearGradient id="grad1" x1="2" y1="2" x2="22" y2="22" gradientUnits="userSpaceOnUse">
                      <stop stop-color="#4E75F6"/>
                      <stop offset="1" stop-color="#E3557A"/>
                    </linearGradient>
                  </defs>
                </svg>
              </div>
              <div class="ai-content-wrapper">
                <!-- Logs / Thinking Process -->
                <div v-if="msg.logs && msg.logs.length > 0" class="process-section">
                  <details>
                    <summary>
                      <span class="spinner" v-if="status === 'running' && index === messages.length - 1"></span>
                      View thought process
                    </summary>
                    <div class="process-steps">
                      <div v-for="(log, idx) in msg.logs" :key="idx" class="step-item">
                        <div class="step-header">
                          <span class="step-icon">🔧</span>
                          <span class="step-title">{{ log.title }}</span>
                        </div>
                        <div class="step-details" v-if="log.details">
                          <pre>{{ JSON.stringify(log.details, null, 2) }}</pre>
                        </div>
                      </div>
                    </div>
                  </details>
                </div>

                <!-- Text Content -->
                <div class="markdown-body" v-html="renderMarkdown(msg.content)"></div>

                <!-- Files -->
                <div v-if="msg.files && msg.files.length > 0" class="files-grid">
                  <a v-for="file in msg.files" :key="file.name" :href="file.url" target="_blank" class="file-card"
                     :download="file.name">
                    <div class="file-icon">📄</div>
                    <div class="file-info">
                      <div class="file-name">{{ file.name }}</div>
                      <div class="file-type">Document</div>
                    </div>
                  </a>
                </div>

               <!-- 高危操作审批卡片 -->
                <div v-if="msg.approvalRequests && msg.approvalRequests.length > 0" class="approval-card">
                  <div class="approval-header">
                    <!-- 警告图标 -->
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <path d="M12 2L1 21H23L12 2ZM12 18C11.45 18 11 17.55 11 17C11 16.45 11.45 16 12 16C12.55 16 13 16.45 13 17C13 17.55 12.55 18 12 18ZM13 15H11V10H13V15Z" fill="currentColor"/>
                    </svg>
                    系统拦截到高危操作，等待您的授权
                  </div>
                  <div class="approval-body">
                    <div v-for="(req, idx) in msg.approvalRequests" :key="idx" class="approval-req-item">
                      <div class="req-tool">工具：{{ req.name }}</div>
                      <pre class="req-args">{{ JSON.stringify(req.args, null, 2) }}</pre>
                    </div>
                  </div>
                  <div class="approval-actions" v-if="!msg.isApproved">
                    <button class="btn-reject" @click="sendApproval(index, msg.approvalRequests[0].name, false)">拒绝 (Reject)</button>
                    <button class="btn-approve" @click="sendApproval(index, msg.approvalRequests[0].name, true)">同意执行 (Approve)</button>
                  </div>
                  <div v-else class="approval-status">
                    ✅ 已提交审批决策，继续执行中...
                  </div>
                </div>
              </div>

            </div>

            <!-- System Message -->
            <div v-else class="message-system">
              {{ msg.content }}
            </div>

          </div>
          <div ref="messagesEndRef" class="spacer-bottom"></div>
        </div>
      </div>


      <!-- 任务规划 (To_do) 悬浮组件  -->
      <div v-if="!isWelcomeScreen && activeTodos.length > 0" class="todo-floating-panel"
           :class="{ 'collapsed': !isTodoPanelOpen }">
        <div class="todo-header" @click="isTodoPanelOpen = !isTodoPanelOpen">
          <span class="todo-title">📋 任务规划与进度</span>
          <span class="todo-toggle">{{ isTodoPanelOpen ? '▼' : '▲' }}</span>
        </div>
        <div v-if="isTodoPanelOpen" class="todo-body">
          <div v-for="(todo, idx) in activeTodos" :key="idx" class="todo-item" :class="todo.status">
            <!-- 状态图标 -->
            <div class="todo-icon">
              <span v-if="todo.status === 'completed'" class="icon-success">✅</span>
              <span v-else-if="todo.status === 'in_progress'" class="icon-loading spinner-small">⚙️</span>
              <span v-else class="icon-pending">⏳</span>
            </div>
            <!-- 任务内容 -->
            <div class="todo-content">{{ todo.content }}</div>
          </div>
        </div>
      </div>

      <!-- Input Area -->
      <footer class="input-footer">
        <!-- File Preview Tab -->
        <div v-if="selectedFiles.length > 0" class="file-preview-container">
          <div v-for="(file, index) in selectedFiles" :key="index" class="file-preview-chip">
            <span class="file-preview-icon">📎</span>
            <span class="file-preview-name">{{ file.name }}</span>
            <button class="file-remove-btn" @click="removeFile(index)" title="Remove file">×</button>
          </div>
        </div>

        <div class="input-container" :class="{ focused: status === 'running' }">
          <input
              type="file"
              ref="fileInputRef"
              multiple
              style="display: none"
              @change="handleFileChange"
          />
          <button class="upload-btn" @click="triggerFileUpload" :disabled="status === 'running'" title="Upload file">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path
                  d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"
                  stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
          <textarea
              v-model="inputQuery"
              @keydown.enter.exact.prevent="sendMessage"
              placeholder="Enter a prompt here"
              :disabled="status === 'running'"
          ></textarea>
          <button class="send-btn" @click="sendMessage" :disabled="!inputQuery.trim() && status !== 'running'">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"></path>
            </svg>
          </button>
        </div>
        <div class="footer-text">
          DeepAgents may display inaccurate info, including about people, so double-check its responses.
        </div>
      </footer>
    </main>

    <!-- Right Sidebar (File Explorer) -->
    <aside v-if="isSidebarOpen" class="file-sidebar">
      <div class="sidebar-header">
        <h3>Session Files</h3>
        <div style="display: flex; gap: 8px; align-items: center;">
          <button class="folder-btn" @click="fetchFiles" title="Refresh Files" style="padding: 4px 8px;">
            ↻
          </button>
          <button class="close-btn" @click="isSidebarOpen = false">×</button>
        </div>
      </div>
      <div class="file-list">
        <div v-if="fileList.length === 0" class="empty-files">
          No files generated yet.
        </div>
        <div v-else v-for="file in fileList" :key="file.path" class="file-item">
          <a :href="file.url" target="_blank" class="file-link" :download="file.name">
            <span class="file-icon">📄</span>
            <span class="file-name-text">{{ file.name }}</span>
          </a>
        </div>
      </div>
    </aside>
  </div>
</template>

<style>
/* Global Resets & Variables */
:root {
  --bg-dark: #131314;
  --surface-dark: #1E1F20;
  --surface-light: #2D2E2F;
  --text-primary: #E3E3E3;
  --text-secondary: #C4C7C5;
  --accent-blue: #A8C7FA;
  --user-msg-bg: #2D2E30; /* Darker gray for user */
  --border-color: #444746;
}

body {
  margin: 0;
  background-color: var(--bg-dark);
  color: var(--text-primary);
  font-family: 'Google Sans', 'Roboto', Helvetica, Arial, sans-serif;
  overflow: hidden; /* App handles scroll */
}

/* Layout */
.app-container {
  display: flex;
  height: 100vh;
  width: 100vw;
}

/* Main Content */
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  position: relative;
  background-color: var(--bg-dark);
  min-width: 0; /* Prevent flex overflow */
}

.sidebar-toggle-btn {
  position: absolute;
  top: 1rem;
  right: 1rem;
  background: transparent;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  z-index: 10;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.sidebar-toggle-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-primary);
}

/* File Sidebar */
.file-sidebar {
  width: 300px;
  background-color: var(--surface-dark);
  border-left: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.sidebar-header {
  padding: 1rem;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sidebar-header h3 {
  margin: 0;
  font-size: 1rem;
  font-weight: 500;
  color: var(--text-primary);
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1.5rem;
  cursor: pointer;
  padding: 0;
  line-height: 1;
  transition: color 0.2s;
}

.close-btn:hover {
  color: var(--text-primary);
}

.file-list {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.empty-files {
  color: var(--text-secondary);
  text-align: center;
  font-size: 0.9rem;
  margin-top: 2rem;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.file-link {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem;
  border-radius: 8px;
  color: var(--text-primary);
  text-decoration: none;
  transition: all 0.2s ease;
  border: 1px solid transparent;
}

.file-link:hover {
  background: rgba(255,255,255,0.05);
  border-color: #444;
  transform: translateX(2px);
}

.folder-btn {
  background: transparent;
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
  cursor: pointer;
  padding: 0.5rem;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.folder-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-primary);
  border-color: #888;
}

.file-name-text {
  font-size: 0.9rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Welcome Screen */
.welcome-screen {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 2rem;
}

/* Centered Layout Mode (Initial State) */
.main-content.centered-layout {
  justify-content: center;
  align-items: center;
  overflow-y: auto;
}

.main-content.centered-layout .welcome-screen {
  flex: 0 0 auto;
  padding-bottom: 2rem;
}

.main-content.centered-layout .input-footer {
  width: 100%;
  max-width: 100%;
  padding: 0;
  background: transparent;
  justify-content: center;
}

.welcome-text {
  text-align: center;
  margin-bottom: 2rem;
}

.welcome-text h1 {
  font-size: 3.5rem;
  background: linear-gradient(90deg, #4E75F6, #E3557A);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  margin: 0;
  line-height: 1.2;
}

.welcome-text h2 {
  font-size: 3.5rem;
  color: #555;
  margin: 0;
  line-height: 1.2;
}

/* Chat Area */
.chat-scroll-area {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.chat-container {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2rem;
}

.message-wrapper {
  display: flex;
  flex-direction: column;
  width: 100%;
}

/* User Message */
.message-user {
  align-self: flex-end;
  max-width: 75%;
}

.msg-content {
  /* 优化用户气泡，增加极弱的渐变质感 */
  background: linear-gradient(135deg, #2D2E30, #36383A);
  padding: 12px 18px;
  border-radius: 20px;
  border-bottom-right-radius: 4px;
  line-height: 1.6;
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
}

/* AI Message */
.message-ai {
  align-self: flex-start;
  width: 100%;
  display: flex;
  gap: 1rem;
}

.ai-avatar {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  margin-top: 4px;
}

.ai-content-wrapper {
  flex: 1;
  min-width: 0; /* Text wrap fix */
}

.markdown-body {
  line-height: 1.6;
  font-size: 1rem;
}

.markdown-body pre {
  background: #1A1A1A;
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
  border: 1px solid #333;
}

.typing-indicator {
  color: var(--text-secondary);
  font-style: italic;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0% { opacity: 0.4; }
  50% { opacity: 1; }
  100% { opacity: 0.4; }
}

/* Process / Logs */
.process-section {
  margin-bottom: 1rem;
}

.process-section summary {
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 0.85rem;
  list-style: none; /* Hide default arrow */
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  background: rgba(255,255,255,0.02);
  border: 1px solid transparent;
  transition: all 0.2s ease;
  width: max-content;
}

.process-section summary:hover {
  background: rgba(255,255,255,0.06);
  border-color: #444;
}

.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--accent-blue);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.process-steps {
  background: #1E1F20;
  border-radius: 8px;
  padding: 0.75rem;
  margin-top: 0.5rem;
  border: 1px solid #333;
  box-shadow: inset 0 2px 8px rgba(0,0,0,0.2);
}

.step-item {
  padding: 0.5rem;
  border-left: 2px solid #444;
  margin-left: 0.5rem;
  margin-bottom: 0.75rem;
  transition: border-color 0.2s;
}
.step-item:hover {
  border-color: var(--accent-blue);
}

.step-header {
  font-size: 0.85rem;
  font-weight: 500;
  color: #E3E3E3;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.step-details pre {
  margin: 0.5rem 0 0 0;
  font-size: 0.75rem;
  color: #A8C7FA; /* 调亮参数代码颜色 */
  background: #111;
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
  border: 1px solid #2A2A2A;
}

/* Files Grid */
.files-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1rem;
}

.file-card {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background: #252628;
  padding: 0.75rem 1rem;
  border-radius: 10px;
  text-decoration: none;
  color: var(--text-primary);
  border: 1px solid #444;
  transition: all 0.2s ease;
  min-width: 160px;
}

.file-card:hover {
  background: #2D2E30;
  border-color: var(--accent-blue);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
}

.file-info {
  display: flex;
  flex-direction: column;
}

.file-name {
  font-weight: 500;
  font-size: 0.9rem;
}

.file-type {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

/* System Message */
.message-system {
  text-align: center;
  font-size: 0.8rem;
  color: #888;
  margin: 1.5rem 0;
  font-style: italic;
}

.spacer-bottom {
  height: 100px;
}

/* Input Footer */
.input-footer {
  background: var(--bg-dark); /* Ensure it covers scrolling content */
  padding: 1rem 2rem 1.5rem 2rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  z-index: 20;
}

.input-container {
  width: 100%;
  max-width: 800px;
  background: #1E1F20;
  border-radius: 24px;
  display: flex;
  align-items: center;
  padding: 0.5rem 1rem;
  border: 1px solid transparent;
  transition: all 0.3s ease;
  box-shadow: 0 2px 6px rgba(0,0,0,0.1);
}

/* 输入框发光焦点态 */
.input-container.focused,
.input-container:focus-within {
  background: #252628;
  border-color: rgba(168, 199, 250, 0.3);
  box-shadow: 0 0 0 2px rgba(168, 199, 250, 0.1), 0 4px 12px rgba(0,0,0,0.2);
}

textarea {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--text-primary);
  font-size: 1rem;
  padding: 10px;
  resize: none;
  height: 24px;
  max-height: 200px;
  font-family: inherit;
  outline: none;
  line-height: 1.5;
}

.send-btn {
  background: none;
  border: none;
  color: var(--text-primary);
  cursor: pointer;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.send-btn:disabled {
  color: #444746;
  cursor: default;
}

.send-btn:not(:disabled):hover {
  background: rgba(255,255,255,0.1);
  color: var(--accent-blue);
}

.upload-btn {
  background: none;
  border: none;
  color: var(--text-primary);
  cursor: pointer;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 4px;
  transition: all 0.2s;
}

.upload-btn:hover:not(:disabled) {
  background: rgba(255,255,255,0.1);
}

.upload-btn:disabled {
  color: #444746;
  cursor: default;
}

.footer-text {
  font-size: 0.75rem;
  color: #555;
  text-align: center;
}

/* File Preview Styles */
.file-preview-container {
  width: 100%;
  max-width: 800px;
  display: flex;
  justify-content: flex-start;
  padding-left: 1rem;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.file-preview-chip {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: #252628;
  padding: 0.5rem 0.8rem;
  border-radius: 8px;
  border: 1px solid #444;
  font-size: 0.85rem;
  color: var(--text-primary);
  animation: slideUp 0.2s ease-out;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

.file-preview-icon {
  font-size: 1rem;
}

.file-preview-name {
  max-width: 200px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-remove-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 1.1rem;
  padding: 0 4px;
  line-height: 1;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.file-remove-btn:hover {
  background: rgba(255, 82, 82, 0.15);
  color: #FF5252;
}

/* ====== To_do 悬浮面板高级样式 ====== */
.todo-floating-panel {
  position: absolute;
  top: 1.5rem;
  left: 1.5rem;
  width: 320px;
  max-width: calc(100vw - 3rem);

  /* 增强毛玻璃质感，加入顶部微光边缘 */
  background-color: rgba(30, 31, 32, 0.75);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-top: 1px solid rgba(255, 255, 255, 0.15);

  border-radius: 14px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  z-index: 100;
  overflow: hidden;
  transition: all 0.3s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.todo-floating-panel.collapsed {
  width: auto;
  min-width: 160px;
  border-radius: 20px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
}

.todo-header {
  padding: 0.6rem 1rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.03);
  transition: background 0.2s;
}

.todo-header:hover {
  background: rgba(255, 255, 255, 0.08);
}

.todo-title {
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--accent-blue);
  letter-spacing: 0.5px;
}

.todo-body {
  padding: 0.75rem;
  border-top: 1px solid rgba(255,255,255,0.05);
  max-height: 350px;
  overflow-y: auto;
}

.todo-item {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.6rem;
  border-radius: 8px;
  margin-bottom: 0.4rem;
  background: rgba(45, 46, 48, 0.6);
  border-left: 3px solid transparent;
  transition: all 0.2s ease;
}
.todo-item:hover {
  background: rgba(60, 62, 64, 0.8);
  transform: translateX(2px);
}

.todo-item.completed {
  border-left-color: #4CAF50;
  opacity: 0.6;
}
.todo-item.completed .todo-content {
  text-decoration: line-through;
  color: #777;
}

.todo-item.in_progress {
  border-left-color: var(--accent-blue);
  background: rgba(33, 150, 243, 0.1);
}
.todo-item.in_progress .todo-content {
  color: #FFF;
  font-weight: 500;
}

.todo-item.pending {
  border-left-color: #FF9800;
  opacity: 0.8;
}
.todo-item.pending .todo-content {
  color: #AAA;
}

.todo-icon {
  flex-shrink: 0;
  font-size: 1rem;
  margin-top: 2px;
}

.todo-content {
  font-size: 0.85rem;
  line-height: 1.4;
}

/* ====== 🚨 高危操作审批卡片样式 ====== */
.approval-card {
  margin-top: 1rem;
  background: #1C1917; /* 极深灰略带暖色 */
  border: 1px solid rgba(255, 152, 0, 0.6);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
  animation: slideUp 0.3s ease-out;
}

.approval-header {
  background: linear-gradient(90deg, rgba(255, 152, 0, 0.15), rgba(255, 152, 0, 0.05));
  color: #FFB74D;
  padding: 0.8rem 1rem;
  font-weight: 600;
  font-size: 0.95rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  border-bottom: 1px solid rgba(255, 152, 0, 0.2);
}

.approval-body {
  padding: 1rem;
}

.approval-req-item {
  margin-bottom: 0.5rem;
}

.req-tool {
  display: inline-block;
  background: rgba(255,255,255,0.08);
  color: var(--accent-blue);
  padding: 4px 10px;
  border-radius: 6px;
  font-family: 'Consolas', monospace;
  font-size: 0.85rem;
  margin-bottom: 0.75rem;
  border: 1px solid rgba(255,255,255,0.1);
}

.req-args pre {
  background: #0D0D0D;
  color: #D4D4D4;
  padding: 1rem;
  border-radius: 8px;
  font-size: 0.85rem;
  overflow-x: auto;
  margin: 0;
  border: 1px solid #222;
  font-family: 'Consolas', 'Monaco', monospace;
  line-height: 1.5;
}

.approval-actions {
  padding: 1rem;
  background: rgba(0,0,0,0.2);
  border-top: 1px solid #2A2A2A;
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
}

.approval-actions button {
  padding: 0.6rem 1.4rem;
  border-radius: 8px;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.btn-reject {
  background: transparent;
  color: #FF5252;
  border: 1px solid rgba(255, 82, 82, 0.4);
}

.btn-reject:hover {
  background: rgba(255, 82, 82, 0.1);
  border-color: #FF5252;
  transform: translateY(-1px);
}

.btn-approve {
  background: linear-gradient(135deg, #4CAF50, #43A047);
  color: #fff;
  border: none;
  box-shadow: 0 2px 8px rgba(76, 175, 80, 0.2);
}

.btn-approve:hover {
  box-shadow: 0 4px 12px rgba(76, 175, 80, 0.4);
  transform: translateY(-1px);
  filter: brightness(1.1);
}

.approval-status {
  padding: 1rem;
  background: rgba(76, 175, 80, 0.08);
  color: #81C784;
  font-weight: 500;
  text-align: center;
  border-top: 1px solid #2A2A2A;
}

/* 瘦身并优化滚动条 */
::-webkit-scrollbar {
  width: 6px; /* 变细 */
  height: 6px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15); /* 柔和半透明色 */
  border-radius: 10px;
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.25);
}
</style>
