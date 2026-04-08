# 企业微信客户消息自动回复

自动回复企业微信**外部客户消息**的 Python 应用。

## 功能

- 接收企业微信**微信客服（KF）**回调消息
- **混合回复模式**：先匹配关键词规则，无匹配则调用 Claude AI 智能兜底
- Streamlit 管理界面：可视化管理回复规则 + 查看消息日志
- SQLite 持久化消息记录

## 技术栈

| 模块 | 技术 |
|------|------|
| API 服务 | FastAPI + Uvicorn |
| 消息解密 | wechatpy (WXBizMsgCrypt AES-256) |
| AI 回复 | Anthropic Claude (claude-haiku-4-5) |
| 管理界面 | Streamlit |
| 消息存储 | SQLAlchemy + SQLite |

## 项目结构

```
wechat-autoreply/
├── main.py                    # FastAPI 入口（GET/POST /callback）
├── config.py                  # 配置加载（.env）
├── wecom/
│   ├── crypto.py              # 签名验证 + AES 解密
│   ├── client.py              # access_token 缓存 + 消息收发
│   └── callback_handler.py   # 消息解析 → 回复引擎 → 发送
├── reply/
│   ├── engine.py              # 混合回复协调器
│   ├── rules.py               # 关键词规则引擎（支持热更新）
│   └── claude_client.py      # Claude API 封装
├── storage/
│   ├── db.py                  # SQLAlchemy engine
│   └── message_log.py        # 消息日志模型
├── admin/
│   └── app.py                 # Streamlit 管理界面
├── rules.json                 # 关键词 → 回复规则配置
├── .env.example               # 配置模板
└── SETUP.md                   # 企业微信后台配置手册
```

## 快速开始

### 1. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入企业微信和 Claude 凭证
```

> 详细配置步骤见 [SETUP.md](SETUP.md)

### 3. 启动服务

```bash
# API 服务（端口 8000）
uvicorn main:app --reload --port 8000

# 暴露公网（ngrok，用于企业微信回调）
ngrok http 8000

# 管理界面（端口 8501）
streamlit run admin/app.py --server.port 8501
```

## 回复规则配置

编辑 `rules.json` 或通过管理界面操作。支持三种匹配类型：

| match_type | 说明 | 示例 keyword/pattern |
|-----------|------|---------------------|
| `exact` | 精确匹配 | `"退款流程"` |
| `contains` | 包含匹配 | `"营业时间"` |
| `regex` | 正则匹配 | `"(价格\|多少钱\|报价)"` |

优先级按 `priority` 升序，首个命中规则生效；全部未命中时调用 Claude AI。

## 数据流

```
外部客户发消息
    ↓ WeCom KF 回调推送
FastAPI POST /callback
    ↓ 验签 + AES 解密
sync_kf_messages（拉取实际消息内容）
    ↓
关键词规则匹配 → 命中则直接回复
    ↓ 未命中
Claude AI 生成回复
    ↓
POST /kf/send_msg → 客户收到回复
```
