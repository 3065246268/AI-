#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
AI 工具箱 - 优化版
================================================================================
版本号：v2.0.3
描述：AI 智能助手平台 - 集智能客服、OCR 识别、数字识别、语音合成于一体

功能模块：
  - 智能客服（AI Chat）：基于智谱 GLM-4 大语言模型的多轮对话
  - OCR 识别：印刷文字和手写文字识别，准确率 95%+
  - 数字识别：手写数字识别，支持 0-100 范围  准确率 75%+
  - 图文朗读：OCR + TTS 一键完成
  - 语音合成：四种音色，文字转语音

更新日志：
  v2.0.0 - 2026-04-02
    - 新增图文朗读功能（OCR + TTS 一键完成）
    - 配置热更新（修改 API Key 后自动刷新 Token）
    - 前端 XSS 防护
    - UUID 会话管理
    - 统一错误处理
================================================================================
"""
import os, base64, json, re, uuid, requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

BASE_DIR = r"C:\Users\Wang\.qclaw\workspace"
CONFIG_FILE = os.path.join(BASE_DIR, "ai_toolbox_config.json")
BAIDU_OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
BAIDU_DIGIT_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/numbers"
BAIDU_TTS_URL = "https://tsn.baidu.com/text2audio"
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_VISION_URL = "https://aip.baidubce.com/rest/2.0/image-classify/v2/advanced_general"  # 图像识别（通用物体和场景）
BAIDU_ANIMAL_URL = "https://aip.baidubce.com/rest/2.0/image-classify/v1/animal"  # 动物识别
BAIDU_PLANT_URL = "https://aip.baidubce.com/rest/2.0/image-classify/v1/plant"  # 植物识别
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        cfg = {"baidu": {"api_key": "", "secret_key": ""}, "zhipu": {"api_key": ""}}
        save_config(cfg)
        return cfg

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def check_api_keys():
    c = load_config()
    bk = c.get("baidu", {})
    zk = c.get("zhipu", {})
    baidu_ok = bool(bk.get("api_key") and bk.get("secret_key") and "请填写" not in bk.get("api_key", ""))
    zhipu_ok = bool(zk.get("api_key") and "请填写" not in zk.get("api_key", ""))
    return baidu_ok, zhipu_ok

class ZhipuService:
    SYSTEM_PROMPT = {"role": "system", "content": "你是一位专业、友好、耐心的 AI 助手。请用中文回答，语气亲切自然，简洁明了。"}
    MAX_HISTORY = 30

    def __init__(self):
        self.sessions = {}

    def _get_api_key(self):
        return load_config().get("zhipu", {}).get("api_key", "")

    def _init_session(self, sid):
        if sid not in self.sessions:
            self.sessions[sid] = [self.SYSTEM_PROMPT.copy()]

    def _trim(self, msgs):
        if len(msgs) > self.MAX_HISTORY:
            return [msgs[0]] + msgs[-(self.MAX_HISTORY - 1):]
        return msgs

    def chat(self, sid, user_input):
        api_key = self._get_api_key()
        if not api_key or "请填写" in api_key:
            return "请先在【配置】页面填写智谱 API Key"
        self._init_session(sid)
        msgs = self.sessions[sid]
        msgs.append({"role": "user", "content": user_input})
        msgs = self._trim(msgs)
        try:
            resp = requests.post(
                ZHIPU_API_URL,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                json={"model": "glm-4-flash", "messages": msgs, "temperature": 0.7, "max_tokens": 1000},
                timeout=90
            )
            if resp.status_code == 401:
                return "API Key 无效，请检查配置"
            if resp.status_code == 429:
                return "请求过于频繁，请稍后重试"
            if resp.status_code != 200:
                return f"请求失败：HTTP {resp.status_code}"
            result = resp.json()
            reply = result["choices"][0]["message"]["content"]
            msgs.append({"role": "assistant", "content": reply})
            self.sessions[sid] = msgs
            return reply
        except requests.exceptions.Timeout:
            return "请求超时，请检查网络连接或稍后重试"
        except requests.exceptions.ConnectionError:
            return "无法连接到智谱 AI 服务器，请检查网络"
        except Exception as e:
            return f"请求失败：{str(e)}"

    def clear(self, sid):
        if sid in self.sessions:
            self.sessions[sid] = [self.SYSTEM_PROMPT.copy()]


class BaiduService:
    def __init__(self):
        self._token = None
        self._token_expiry = 0
        self._config_hash = None

    def _get_config(self):
        return load_config()

    def _should_refresh(self):
        cfg = self._get_config()
        h = hash(json.dumps(cfg.get("baidu", {}), sort_keys=True))
        if h != self._config_hash:
            self._config_hash = h
            return True
        return False

    def _get_token(self):
        import hashlib as _hm
        if self._should_refresh():
            self._token = None
            self._token_expiry = 0
        if self._token and datetime.now().timestamp() < self._token_expiry:
            return self._token
        cfg = self._get_config()
        ak = cfg.get("baidu", {}).get("api_key", "")
        sk = cfg.get("baidu", {}).get("secret_key", "")
        if not ak or not sk or "请填写" in ak or "请填写" in sk:
            return None
        try:
            r = requests.post(
                BAIDU_TOKEN_URL,
                params={"grant_type": "client_credentials", "client_id": ak, "client_secret": sk},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15
            )
            if not r.text.strip():
                return None
            result = r.json()
            if "access_token" in result:
                self._token = result["access_token"]
                self._token_expiry = datetime.now().timestamp() + result.get("expires_in", 2592000) - 3600
                return self._token
            return None
        except:
            return None

    def ocr(self, img_b64):
        tok = self._get_token()
        if not tok:
            return {"error": "请先配置百度 AI API Key 和 Secret Key"}
        try:
            r = requests.post(
                BAIDU_OCR_URL,
                data={"image": img_b64, "language_type": "CHN_ENG", "detect_direction": "false", "detect_language": "false", "probability": "true"},
                params={"access_token": tok},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60
            )
            raw = r.text.strip()
            if not raw:
                return {"error": "OCR 识别失败：百度 API 返回了空响应，请检查网络或 API 配额"}
            try:
                result = r.json()
            except Exception:
                return {"error": f"OCR 识别失败：响应格式错误（{r.status_code}），请检查 API Key 是否正确"}
            if "error_code" in result:
                return {"error": f"OCR 识别失败：百度 API 错误 {result.get('error_code')} - {result.get('error_msg', '')}"}
            if "words_result" in result:
                words = [item.get("words", "") for item in result["words_result"] if item.get("words")]
                if words:
                    result["combined_text"] = "".join(words)
                    nums = re.findall(r"\d+", result["combined_text"])
                    if nums:
                        result["extracted_numbers"] = nums
            return result
        except requests.exceptions.Timeout:
            return {"error": "OCR 识别超时，请稍后重试"}
        except Exception as e:
            return {"error": f"OCR 识别失败：{str(e)}"}

    def recognize_digits(self, img_b64):
        tok = self._get_token()
        if not tok:
            return {"error": "请先配置百度 AI API Key 和 Secret Key"}
        try:
            r = requests.post(
                BAIDU_DIGIT_URL,
                data={"image": img_b64, "detect_direction": "false"},
                params={"access_token": tok},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60
            )
            raw = r.text.strip()
            if not raw:
                return {"error": "数字识别失败：百度 API 返回了空响应，请检查网络或 API 配额"}
            try:
                result = r.json()
            except Exception:
                return {"error": f"数字识别失败：响应格式错误，请检查 API Key 是否正确"}
            if "error_code" in result:
                return {"error": f"数字识别失败：百度 API 错误 {result.get('error_code')} - {result.get('error_msg', '')}"}
            return result
        except requests.exceptions.Timeout:
            return {"error": "数字识别超时，请稍后重试"}
        except Exception as e:
            return {"error": f"数字识别失败：{str(e)}"}

    def tts(self, text, voice="1"):
        tok = self._get_token()
        if not tok:
            return {"error": "请先配置百度 AI API Key 和 Secret Key"}
        import re, urllib.parse
        # 严格清理：只保留中文、英文、数字、常用标点
        text = re.sub(r"[\r\n]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        # 只保留白名单字符
        text = re.sub(
            r"[^\u4e00-\u9fa5a-zA-Z0-9，。、！？；：""''（）【】《》·,.!?;:\'\"()\[\]<>\- ]",
            "", text
        )
        text = text[:512]  # 限制字数
        if not text.strip():
            return {"error": "识别结果无可朗读内容"}
        try:
            params = urllib.parse.urlencode({
                "tex": text, "tok": tok, "cuid": "ai_toolbox",
                "ctp": "1", "lan": "zh", "spd": "5", "pit": "5", "vol": "5", "per": voice
            })
            r = requests.post(BAIDU_TTS_URL, data=params.encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                timeout=60)
            ct = r.headers.get("Content-Type", "")
            if "audio" in ct and len(r.content) > 0:
                return {"success": True, "audio": base64.b64encode(r.content).decode("utf-8")}
            # 非音频 -> 解析错误信息
            try:
                err = r.json()
                err_msg = err.get("err_msg", err.get("error_msg", str(err)))
                return {"error": "TTS 合成失败：" + err_msg}
            except Exception:
                raw = r.text[:200]
                return {"error": "TTS 合成失败（" + str(r.status_code) + "）：" + raw}
        except requests.exceptions.Timeout:
            return {"error": "语音合成超时，请稍后重试"}
        except Exception as e:
            return {"error": "语音合成失败：" + str(e)}

    def vision(self, img_b64):
        """图像识别 - 识别图片中的物体和场景"""
        tok = self._get_token()
        if not tok:
            return {"error": "请先配置百度 AI API Key 和 Secret Key"}
        try:
            r = requests.post(
                BAIDU_VISION_URL,
                data={"image": img_b64},
                params={"access_token": tok},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60
            )
            raw = r.text.strip()
            if not raw:
                return {"error": "图像识别失败：百度 API 返回了空响应，请检查网络或 API 配额"}
            try:
                result = r.json()
            except Exception:
                return {"error": f"图像识别失败：响应格式错误（{r.status_code}）：{raw[:300]}"}
            if "error_code" in result:
                return {"error": f"图像识别失败：百度 API 错误 {result.get('error_code')} - {result.get('error_msg', '')}"}
            # 解析识别结果
            if "result" in result and result["result"]:
                items = result["result"]
                desc = []
                for item in items[:10]:  # 取前10个
                    keyword = item.get("keyword", "")
                    score = item.get("score", 0)
                    if keyword:
                        desc.append(f"{keyword} ({score*100:.1f}%)")
                if desc:
                    return {"result": "识别到的物体/场景：\n" + "\n".join(desc)}
                return {"result": "未识别到具体物体"}
            return {"result": "未识别到内容"}
        except requests.exceptions.Timeout:
            return {"error": "图像识别超时，请稍后重试"}
        except Exception as e:
            return {"error": "图像识别失败：" + str(e)}

    def animal(self, img_b64):
        """动物识别 - 识别图片中的动物品种"""
        tok = self._get_token()
        if not tok:
            return {"error": "请先配置百度 AI API Key 和 Secret Key"}
        try:
            r = requests.post(
                BAIDU_ANIMAL_URL,
                data={"image": img_b64, "top_num": 5, "baike_num": 1},
                params={"access_token": tok},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60
            )
            raw = r.text.strip()
            if not raw:
                return {"error": "动物识别失败：百度 API 返回了空响应"}
            try:
                result = r.json()
            except Exception:
                return {"error": f"动物识别失败：响应格式错误（{r.status_code}）：{raw[:300]}"}
            if "error_code" in result:
                return {"error": f"动物识别失败：百度 API 错误 {result.get('error_code')} - {result.get('error_msg', '')}"}
            if "result" in result and result["result"]:
                items = result["result"]
                desc = []
                for item in items[:5]:
                    name = item.get("name", "")
                    score = float(item.get("score", 0))
                    baike = item.get("baike_info", {})
                    if baike:
                        desc_str = baike.get("description", "")
                        if desc_str and len(desc_str) > 200:
                            desc_str = desc_str[:200] + "..."
                        desc.append(f"🐱 {name} ({score*100:.1f}%)\n   {desc_str}" if desc_str else f"🐱 {name} ({score*100:.1f}%)")
                    elif name:
                        desc.append(f"🐱 {name} ({score*100:.1f}%)")
                if desc:
                    return {"result": "识别到的动物：\n" + "\n".join(desc)}
                return {"result": "未识别到具体动物"}
            return {"result": "未识别到动物"}
        except requests.exceptions.Timeout:
            return {"error": "动物识别超时，请稍后重试"}
        except Exception as e:
            return {"error": "动物识别失败：" + str(e)}

    def plant(self, img_b64):
        """植物识别 - 识别图片中的植物品种"""
        tok = self._get_token()
        if not tok:
            return {"error": "请先配置百度 AI API Key 和 Secret Key"}
        try:
            r = requests.post(
                BAIDU_PLANT_URL,
                data={"image": img_b64, "top_num": 5, "baike_num": 1},
                params={"access_token": tok},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60
            )
            raw = r.text.strip()
            if not raw:
                return {"error": "植物识别失败：百度 API 返回了空响应"}
            try:
                result = r.json()
            except Exception:
                return {"error": f"植物识别失败：响应格式错误（{r.status_code}）：{raw[:300]}"}
            if "error_code" in result:
                return {"error": f"植物识别失败：百度 API 错误 {result.get('error_code')} - {result.get('error_msg', '')}"}
            if "result" in result and result["result"]:
                items = result["result"]
                desc = []
                for item in items[:5]:
                    name = item.get("name", "")
                    score = float(item.get("score", 0))
                    baike = item.get("baike_info", {})
                    if baike:
                        desc_str = baike.get("description", "")
                        if desc_str and len(desc_str) > 200:
                            desc_str = desc_str[:200] + "..."
                        desc.append(f"🌿 {name} ({score*100:.1f}%)\n   {desc_str}" if desc_str else f"🌿 {name} ({score*100:.1f}%)")
                    elif name:
                        desc.append(f"🌿 {name} ({score*100:.1f}%)")
                if desc:
                    return {"result": "识别到的植物：\n" + "\n".join(desc)}
                return {"result": "未识别到具体植物"}
            return {"result": "未识别到植物"}
        except requests.exceptions.Timeout:
            return {"error": "植物识别超时，请稍后重试"}
        except Exception as e:
            return {"error": "植物识别失败：" + str(e)}


app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 允许最大 16MB 请求体
baidu_service = BaiduService()
zhipu_service = ZhipuService()

def get_session_id():
    sid = request.headers.get("X-Session-ID") or request.cookies.get("session_id")
    return sid or str(uuid.uuid4())


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 工具箱</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:linear-gradient(135deg,#1a1a2e,#16213e);min-height:100vh;color:#fff}
.container{max-width:1200px;margin:0 auto;padding:20px}
.header{text-align:center;padding:30px 0;border-bottom:1px solid rgba(255,255,255,.1);margin-bottom:30px}
.header h1{font-size:32px;margin-bottom:10px}
.header p{color:#888}
.nav{display:flex;justify-content:center;gap:10px;margin-bottom:30px;flex-wrap:wrap}
.nav-btn{padding:12px 24px;background:rgba(255,255,255,.1);border:none;border-radius:8px;color:#fff;cursor:pointer;transition:all .3s}
.nav-btn:hover,.nav-btn.active{background:#07c160;transform:translateY(-2px)}
.panel{background:rgba(255,255,255,.05);border-radius:16px;padding:30px;display:none}
.panel.active{display:block}
.chat-container{height:400px;overflow-y:auto;background:rgba(0,0,0,.3);border-radius:12px;padding:20px;margin-bottom:20px}
.message{margin-bottom:15px;padding:12px 16px;border-radius:12px;max-width:70%;animation:fadeIn .3s}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.message.user{background:#2E86DE;margin-left:auto}
.message.ai{background:rgba(255,255,255,.1)}
.input-area{display:flex;gap:10px;flex-wrap:wrap}
.input-area textarea{flex:1;min-width:200px;padding:12px;border:1px solid rgba(255,255,255,.2);border-radius:8px;background:rgba(0,0,0,.3);color:#fff;font-size:14px;resize:none}
.input-area button{padding:12px 24px;background:#07c160;border:none;border-radius:8px;color:#fff;cursor:pointer;font-weight:bold;transition:all .3s;white-space:nowrap}
.input-area button:hover{transform:translateY(-2px)}
.input-area button:disabled{opacity:.5;cursor:not-allowed}
.form-group{margin-bottom:20px}
.form-group label{display:block;margin-bottom:8px;font-weight:600}
.form-group input,.form-group textarea,.form-group select{width:100%;padding:12px;border:1px solid rgba(255,255,255,.2);border-radius:8px;background:rgba(0,0,0,.3);color:#fff;font-size:14px}
.btn{padding:10px 20px;background:#07c160;border:none;border-radius:8px;color:#fff;cursor:pointer;font-size:14px;margin-right:10px;margin-bottom:10px;transition:all .3s}
.btn:hover{transform:translateY(-2px)}
.btn-secondary{background:#555}
.canvas-container{background:#fff;border-radius:12px;padding:20px;margin-bottom:20px;text-align:center}
#digitCanvas{border:2px solid #07c160;border-radius:8px;cursor:crosshair;touch-action:none}
.result-box{background:rgba(0,0,0,.3);border-radius:12px;padding:20px;margin-top:20px;min-height:80px}
.result-box h3{margin-bottom:10px;color:#07c160}
.config-card{background:rgba(0,0,0,.3);border-radius:12px;padding:20px;margin-bottom:20px}
.config-card h3{color:#07c160;margin-bottom:15px;border-left:3px solid #07c160;padding-left:10px}
.alert{padding:15px;border-radius:8px;margin-bottom:20px}
.alert-warning{background:rgba(255,152,0,.2);border-left:4px solid #FF9800;color:#FFB74D}
.alert-success{background:rgba(76,175,80,.2);border-left:4px solid #4CAF50;color:#81C784}
.quick-questions{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:15px}
.quick-btn{padding:8px 16px;background:rgba(7,193,96,.2);border:1px solid #07c160;border-radius:20px;color:#07c160;cursor:pointer;font-size:13px;transition:all .3s}
.quick-btn:hover{background:#07c160;color:#fff}
.loading{display:inline-block;width:18px;height:18px;border:3px solid rgba(255,255,255,.3);border-radius:50%;border-top-color:#fff;animation:spin 1s linear infinite;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.upload-area{border:2px dashed rgba(255,255,255,.3);border-radius:12px;padding:40px;text-align:center;cursor:pointer;transition:all .3s}
.upload-area:hover{border-color:#07c160;background:rgba(7,193,96,.1)}
.upload-area.dragover{border-color:#07c160;background:rgba(7,193,96,.2)}
pre{white-space:pre-wrap;word-break:break-all;color:#fff}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>AI 工具箱</h1>
        <p>智能客服 | OCR 识别 | 数字识别 | 语音合成</p>
    </div>
    <div id="config-status" style="text-align:center;margin-bottom:20px"></div>
    <div class="nav">
        <button class="nav-btn active" data-panel="chat">智能客服</button>
        <button class="nav-btn" data-panel="ocr">OCR 识别</button>
        <button class="nav-btn" data-panel="digit">数字识别</button>
        <button class="nav-btn" data-panel="tts">语音合成</button>
        <button class="nav-btn" data-panel="ocr-tts">图文朗读</button>
        <button class="nav-btn" data-panel="vision">看图识物</button>
        <button class="nav-btn" data-panel="config">配置</button>
    </div>

    <!-- Chat Panel -->
    <div id="chat-panel" class="panel active">
        <div class="quick-questions">
            <span class="quick-btn" data-msg="你好，介绍一下你自己">自我介绍</span>
            <span class="quick-btn" data-msg="讲个笑话">讲笑话</span>
            <span class="quick-btn" data-msg="如何学习编程？">学编程</span>
        </div>
        <div class="chat-container" id="chatMessages">
            <div class="message ai">你好！我是 AI 智能助手，有什么可以帮您？</div>
        </div>
        <div class="input-area">
            <textarea id="chatInput" rows="2" placeholder="输入您的问题...（Enter 发送，Shift+Enter 换行）"></textarea>
            <button onclick="sendMessage()" id="chatBtn">发送</button>
            <button class="btn btn-secondary" onclick="clearChat()">清空</button>
        </div>
    </div>

    <!-- OCR Panel -->
    <div id="ocr-panel" class="panel">
        <h2>OCR 文字识别</h2>
        <p style="color:#888;margin:10px 0 20px">上传图片或拖拽到下方区域，自动提取文字</p>
        <div class="upload-area" id="ocrUploadArea">
            <div style="font-size:48px;margin-bottom:10px">上传</div>
            <div>点击或拖拽上传图片</div>
            <div style="color:#888;font-size:12px;margin-top:10px">支持 JPG、PNG、BMP 等格式</div>
            <input type="file" id="ocrImage" accept="image/*" style="display:none">
        </div>
        <img id="ocrPreview" style="max-width:100%;max-height:300px;display:none;margin-top:20px;border-radius:8px">
        <div style="margin-top:20px">
            <button class="btn" onclick="recognizeOCR()">开始识别</button>
            <button class="btn btn-secondary" onclick="clearOCR()">清空</button>
            <button class="btn btn-secondary" onclick="copyResult('ocrResult')">复制结果</button>
        </div>
        <div class="result-box"><h3>识别结果:</h3><pre id="ocrResult"></pre></div>
        <button class="btn btn-secondary" onclick="runDebug()" style="margin-top:10px;font-size:12px">🔍 诊断网络</button>
        <div id="debugResult" style="margin-top:10px;font-size:12px;color:#aaa;white-space:pre-wrap"></div>
    </div>

    <!-- Digit Panel -->
    <div id="digit-panel" class="panel">
        <h2>手写数字识别</h2>
        <p style="color:#888;margin:10px 0 20px">在画布上写数字 (0-100)，或上传数字图片</p>
        <div style="margin-bottom:15px">
            <span style="color:#888;margin-right:10px">识别模式：</span>
            <button class="btn btn-secondary" id="singleBtn" onclick="setRange('single')">0-9 单个数字</button>
            <button class="btn" id="multiBtn" onclick="setRange('multi')">0-100 多位数字（推荐）</button>
        </div>
        <div class="canvas-container">
            <canvas id="digitCanvas" width="300" height="300"></canvas>
        </div>
        <div style="margin-bottom:15px;padding:10px;background:rgba(7,193,96,.1);border-radius:8px;font-size:13px">
            提示：写大一些（占满画布 70% 以上）、笔画清晰、避免连笔
        </div>
        <button class="btn" onclick="recognizeDigit()">识别数字</button>
        <button class="btn btn-secondary" onclick="clearCanvas()">清空画布</button>
        <div class="result-box">
            <h3>识别结果:</h3>
            <div id="digitResult" style="font-size:48px;font-weight:bold;color:#07c160">?</div>
            <div id="digitConfidence" style="color:#888;margin-top:10px"></div>
        </div>
    </div>

    <!-- TTS 面板 -->
    <div id="tts-panel" class="panel">
        <h2>语音合成</h2>
        <div class="form-group">
            <label>输入文字:</label>
            <textarea id="ttsText" rows="4">你好，欢迎使用 AI 工具箱！</textarea>
        </div>
        <div class="form-group">
            <label>音色选择:</label>
            <select id="ttsVoice">
                <option value="1">男声</option>
                <option value="0">女声</option>
                <option value="3">度逍遥</option>
                <option value="4">度丫丫</option>
            </select>
        </div>
        <button class="btn" onclick="synthesize()" id="ttsBtn">合成并播放</button>
        <div id="ttsStatus" style="margin-top:10px"></div>
        <audio id="ttsAudio" style="width:100%;margin-top:15px;display:none" controls></audio>
    </div>

    <!-- 图文朗读面板 -->
    <div id="ocr-tts-panel" class="panel">
        <h2>图文朗读</h2>
        <p style="color:#888;margin:10px 0 20px">上传图片，自动识别文字并朗读，一步到位</p>
        <div class="upload-area" id="ocrTtsUploadArea">
            <div style="font-size:48px;margin-bottom:10px">📷</div>
            <div>点击或拖拽上传图片</div>
            <div style="color:#888;font-size:12px;margin-top:10px">支持 JPG、PNG、BMP 等格式</div>
            <input type="file" id="ocrTtsImage" accept="image/*" style="display:none">
        </div>
        <img id="ocrTtsPreview" style="max-width:100%;max-height:300px;display:none;margin-top:20px;border-radius:8px">
        <div style="margin-top:20px">
            <select id="ocrTtsVoice" style="padding:10px;border:1px solid rgba(255,255,255,.2);border-radius:8px;background:rgba(0,0,0,.3);color:#fff;margin-right:10px">
                <option value="1">男声</option>
                <option value="0">女声</option>
                <option value="3">度逍遥</option>
                <option value="4">度丫丫</option>
            </select>
            <button class="btn" onclick="recognizeAndSpeak()" id="ocrTtsBtn">识别并朗读</button>
        </div>
        <div id="ocrTtsStatus" style="margin-top:10px"></div>
        <div class="result-box">
            <h3>识别文字:</h3>
            <pre id="ocrTtsResult" style="white-space:pre-wrap;word-break:break-all;color:#fff;min-height:60px"></pre>
            <div id="ocrTtsCharCount" style="color:#888;font-size:12px;margin-top:5px"></div>
        </div>
        <audio id="ocrTtsAudio" style="width:100%;margin-top:15px;display:none" controls></audio>
    </div>

    <!-- 看图识物面板 -->
    <div id="vision-panel" class="panel">
        <h2>看图识物</h2>
        <p style="color:#888;margin:10px 0 20px">上传图片，智能识别物体、动物和植物</p>
        <div class="upload-area" id="visionUploadArea">
            <div style="font-size:48px;margin-bottom:10px">🔍</div>
            <div>点击或拖拽上传图片</div>
            <div style="color:#888;font-size:12px;margin-top:10px">支持 JPG、PNG、BMP 等格式</div>
            <input type="file" id="visionImage" accept="image/*" style="display:none">
        </div>
        <img id="visionPreview" style="max-width:100%;max-height:300px;display:none;margin-top:20px;border-radius:8px">
        <div style="margin-top:20px;display:flex;gap:10px;flex-wrap:wrap">
            <button class="btn" onclick="recognizeVision('general')" id="visionBtnGeneral">🔍 通用识别</button>
            <button class="btn" onclick="recognizeVision('animal')" id="visionBtnAnimal">🐱 动物识别</button>
            <button class="btn" onclick="recognizeVision('plant')" id="visionBtnPlant">🌿 植物识别</button>
        </div>
        <div id="visionStatus" style="margin-top:10px"></div>
        <div class="result-box">
            <h3>识别结果:</h3>
            <pre id="visionResult" style="white-space:pre-wrap;word-break:break-all;color:#fff;min-height:60px"></pre>
        </div>
    </div>

    <!-- Config Panel -->
    <div id="config-panel" class="panel">
        <h2>系统配置</h2>
        <div class="alert alert-warning">首次使用必须配置 API Key，否则功能无法使用</div>
        <div class="config-card">
            <h3>百度 AI 开放平台</h3>
            <div class="form-group"><label>API Key:</label><input type="text" id="baiduApiKey" placeholder="请输入 API Key"></div>
            <div class="form-group"><label>Secret Key:</label><input type="text" id="baiduSecret" placeholder="请输入 Secret Key"></div>
            <p style="color:#888;font-size:12px">获取地址：ai.baidu.com → 控制台 → 应用管理</p>
        </div>
        <div class="config-card">
            <h3>智谱 AI</h3>
            <div class="form-group"><label>API Key:</label><input type="text" id="zhipuApiKey" placeholder="请输入 API Key"></div>
            <p style="color:#888;font-size:12px">获取地址：open.bigmodel.cn → 控制台 → API Key 管理</p>
        </div>
        <button class="btn" onclick="saveConfig()">保存配置</button>
        <div id="configStatus" style="margin-top:15px"></div>
    </div>
</div>

<script>
// ===== Global State =====
var digitRange = 'multi';
function uuidv4() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}
var sessionId = localStorage.getItem('ai_sid') || uuidv4();
localStorage.setItem('ai_sid', sessionId);

// ===== XSS Prevention =====
function addMsg(text, isUser) {
    var container = document.getElementById('chatMessages');
    var div = document.createElement('div');
    div.className = 'message ' + (isUser ? 'user' : 'ai');
    div.textContent = text;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// ===== Navigation =====
document.querySelectorAll('.nav-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
        var panelId = btn.dataset.panel;
        document.querySelectorAll('.panel').forEach(function(p) { p.classList.remove('active'); });
        document.querySelectorAll('.nav-btn').forEach(function(b) { b.classList.remove('active'); });
        document.getElementById(panelId + '-panel').classList.add('active');
        btn.classList.add('active');
    });
});

// ===== Quick Questions =====
document.querySelectorAll('.quick-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
        document.getElementById('chatInput').value = btn.dataset.msg;
        sendMessage();
    });
});

// ===== Chat =====
document.getElementById('chatInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

function sendMessage() {
    var input = document.getElementById('chatInput');
    var btn = document.getElementById('chatBtn');
    var text = input.value.trim();
    if (!text) return;

    addMsg(text, true);
    input.value = '';
    btn.disabled = true;
    btn.innerHTML = '<span class="loading"></span>';

    fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-ID': sessionId },
        body: JSON.stringify({ message: text })
    }).then(function(r) { return r.json(); })
      .then(function(res) { addMsg(res.reply || res.error || '无响应', false); })
      .catch(function(e) { addMsg('请求失败：' + e.message, false); })
      .finally(function() {
        btn.disabled = false;
        btn.textContent = '发送';
      });
}

function clearChat() {
    if (confirm('确定清空对话历史？')) {
        fetch('/api/chat/clear', { method: 'POST', headers: { 'X-Session-ID': sessionId } });
        document.getElementById('chatMessages').innerHTML = '<div class="message ai">对话已清空，请问有什么可以帮您？</div>';
    }
}

// ===== Debug =====
async function runDebug() {
    const el = document.getElementById('debugResult');
    el.textContent = '正在诊断...';
    try {
        const r = await fetch('/api/debug');
        const d = await r.json();
        let txt = '=== 诊断结果 ===\\n';
        txt += '百度 API Key: ' + (d.config.baidu_api_key || '未配置') + '\\n';
        txt += '百度 Secret: ' + (d.config.baidu_secret_key || '未配置') + '\\n';
        txt += '智谱 API Key: ' + (d.config.zhipu_api_key || '未配置') + '\\n';
        txt += 'Token 状态: ' + (d.token || '无') + '\\n';
        if (d.token === '获取成功') {
            txt = '✅ 百度 API 连接正常！\\n\\n' + txt;
        } else if (d.token === 'API Key 未配置') {
            txt = '❌ 请先填写百度 API Key 和 Secret Key\\n\\n' + txt;
        } else {
            txt = '⚠️  百度 API 连接异常\\n\\n' + txt;
        }
        el.textContent = txt;
    } catch(e) {
        el.textContent = '诊断请求失败: ' + e.message;
    }
}

// ===== OCR =====
var ocrUploadArea = document.getElementById('ocrUploadArea');
ocrUploadArea.addEventListener('click', function() { document.getElementById('ocrImage').click(); });
ocrUploadArea.addEventListener('dragover', function(e) { e.preventDefault(); ocrUploadArea.classList.add('dragover'); });
ocrUploadArea.addEventListener('dragleave', function() { ocrUploadArea.classList.remove('dragover'); });
ocrUploadArea.addEventListener('drop', function(e) {
    e.preventDefault();
    ocrUploadArea.classList.remove('dragover');
    var files = e.dataTransfer.files;
    if (files && files[0]) {
        document.getElementById('ocrImage').files = files;
        previewOCR(files[0]);
    }
});

document.getElementById('ocrImage').addEventListener('change', function(e) {
    if (e.target.files && e.target.files[0]) previewOCR(e.target.files[0]);
});

function previewOCR(file) {
    var reader = new FileReader();
    reader.onload = function(e) {
        var img = document.getElementById('ocrPreview');
        img.src = e.target.result;
        img.style.display = 'block';
    };
    reader.readAsDataURL(file);
}

function recognizeOCR() {
    var input = document.getElementById('ocrImage');
    var result = document.getElementById('ocrResult');
    if (!input.files || !input.files[0]) { alert('请先上传图片'); return; }
    result.textContent = '识别中...';

    var reader = new FileReader();
    reader.onload = function(e) {
        var b64 = e.target.result.split(',')[1];
        fetch('/api/ocr', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: b64 })
        }).then(function(r) { return r.json(); })
          .then(function(res) {
            if (res.error) result.textContent = res.error;
            else if (res.words_result) result.textContent = res.words_result.map(function(i) { return i.words; }).join('\\n');
            else result.textContent = JSON.stringify(res, null, 2);
          })
          .catch(function(err) { result.textContent = '请求失败：' + err.message; });
    };
    reader.readAsDataURL(input.files[0]);
}

function clearOCR() {
    document.getElementById('ocrImage').value = '';
    document.getElementById('ocrPreview').style.display = 'none';
    document.getElementById('ocrResult').textContent = '';
}

function copyResult(id) {
    var text = document.getElementById(id).textContent;
    if (text) {
        navigator.clipboard.writeText(text).then(function() { alert('已复制到剪贴板！'); }).catch(function() { alert('复制失败'); });
    }
}

// ===== Digit Recognition =====
function setRange(r) {
    digitRange = r;
    document.getElementById('singleBtn').classList.toggle('btn-secondary', r !== 'single');
    document.getElementById('multiBtn').classList.toggle('btn-secondary', r !== 'multi');
}

var canvas = document.getElementById('digitCanvas');
var ctx = canvas.getContext('2d');
ctx.fillStyle = 'white';
ctx.fillRect(0, 0, 300, 300);
ctx.strokeStyle = 'black';
ctx.lineWidth = 20;
ctx.lineCap = 'round';

var painting = false, lastX = 0, lastY = 0, hasDrawn = false;

function getPos(e) {
    var rect = canvas.getBoundingClientRect();
    if (e.touches) {
        return [e.touches[0].clientX - rect.left, e.touches[0].clientY - rect.top];
    }
    return [e.offsetX, e.offsetY];
}

canvas.addEventListener('mousedown', function(e) { painting = true; hasDrawn = true; var p = getPos(e); lastX = p[0]; lastY = p[1]; });
canvas.addEventListener('mouseup', function() { painting = false; ctx.beginPath(); });
canvas.addEventListener('mouseout', function() { painting = false; });
canvas.addEventListener('mousemove', function(e) {
    if (!painting) return;
    var p = getPos(e);
    ctx.beginPath(); ctx.moveTo(lastX, lastY); ctx.lineTo(p[0], p[1]); ctx.stroke();
    lastX = p[0]; lastY = p[1];
});
canvas.addEventListener('touchstart', function(e) { e.preventDefault(); painting = true; hasDrawn = true; var p = getPos(e); lastX = p[0]; lastY = p[1]; });
canvas.addEventListener('touchend', function() { painting = false; ctx.beginPath(); });
canvas.addEventListener('touchmove', function(e) {
    e.preventDefault();
    if (!painting) return;
    var p = getPos(e);
    ctx.beginPath(); ctx.moveTo(lastX, lastY); ctx.lineTo(p[0], p[1]); ctx.stroke();
    lastX = p[0]; lastY = p[1];
});

function clearCanvas() {
    ctx.fillStyle = 'white';
    ctx.fillRect(0, 0, 300, 300);
    document.getElementById('digitResult').textContent = '?';
    document.getElementById('digitConfidence').textContent = '';
    hasDrawn = false;
}

// ===== Vision (看图识物) =====
function initVisionUpload() {
    var area = document.getElementById('visionUploadArea');
    var input = document.getElementById('visionImage');
    if (!area || !input) return;
    area.onclick = function() { input.click(); };
    area.ondragover = function(e) { e.preventDefault(); area.classList.add('dragover'); };
    area.ondragleave = function() { area.classList.remove('dragover'); };
    area.ondrop = function(e) {
        e.preventDefault();
        area.classList.remove('dragover');
        if (e.dataTransfer.files[0]) handleVisionFile(e.dataTransfer.files[0]);
    };
    input.onchange = function() { if (this.files[0]) handleVisionFile(this.files[0]); };
}
function handleVisionFile(file) {
    if (!file.type.startsWith('image/')) { document.getElementById('visionStatus').textContent = '请选择图片文件'; return; }
    var reader = new FileReader();
    reader.onload = function(e) {
        var preview = document.getElementById('visionPreview');
        preview.src = e.target.result;
        preview.style.display = 'block';
    };
    reader.readAsDataURL(file);
}
function recognizeVision(type) {
    type = type || 'general';
    var input = document.getElementById('visionImage');
    if (!input || !input.files[0]) {
        document.getElementById('visionStatus').textContent = '请先上传图片';
        return;
    }
    var apiMap = { general: '/api/vision', animal: '/api/animal', plant: '/api/plant' };
    var nameMap = { general: '通用识别', animal: '动物识别', plant: '植物识别' };
    var btnIds = { general: 'visionBtnGeneral', animal: 'visionBtnAnimal', plant: 'visionBtnPlant' };
    var status = document.getElementById('visionStatus');
    var resultDiv = document.getElementById('visionResult');
    // 禁用所有按钮
    ['visionBtnGeneral','visionBtnAnimal','visionBtnPlant'].forEach(function(id) {
        var b = document.getElementById(id); if(b) b.disabled = true;
    });
    status.textContent = '正在进行' + nameMap[type] + '...';
    resultDiv.textContent = '';
    var reader = new FileReader();
    reader.onload = function(e) {
        var b64 = e.target.result.split(',')[1];
        fetch(apiMap[type], {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: b64 })
        }).then(function(r) { return r.json(); })
          .then(function(res) {
            ['visionBtnGeneral','visionBtnAnimal','visionBtnPlant'].forEach(function(id) {
                var b = document.getElementById(id); if(b) b.disabled = false;
            });
            if (res.error) {
                status.textContent = '错误：' + res.error;
            } else {
                status.textContent = nameMap[type] + '完成';
                resultDiv.textContent = res.result || '未识别到内容';
            }
          }).catch(function(e) {
            ['visionBtnGeneral','visionBtnAnimal','visionBtnPlant'].forEach(function(id) {
                var b = document.getElementById(id); if(b) b.disabled = false;
            });
            status.textContent = '请求失败：' + e.message;
          });
    };
    reader.readAsDataURL(input.files[0]);
}

function recognizeDigit() {
    var result = document.getElementById('digitResult');
    var conf = document.getElementById('digitConfidence');
    if (!hasDrawn) { result.textContent = '?'; conf.textContent = '请先在画布上写字'; return; }
    result.textContent = '...';
    conf.textContent = '识别中...';
    var b64 = canvas.toDataURL('image/png').split(',')[1];

    fetch(digitRange === 'single' ? '/api/digit' : '/api/ocr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: b64 })
    }).then(function(r) { return r.json(); })
      .then(function(res) {
        if (res.error) { result.textContent = '错误'; conf.textContent = res.error; return; }
        var num = null;
        if (digitRange === 'single') {
            if (res.words_result && res.words_result.length > 0) {
                num = res.words_result[0].words;
                var score = res.words_result[0].score || 0;
                conf.textContent = '置信度：' + (score * 100).toFixed(1) + '%';
            }
        } else {
            num = (res.extracted_numbers && res.extracted_numbers[0]) ||
                  (res.words_result && res.words_result.map(function(i) { return i.words; }).join('').match(/\\d+/g) && res.words_result.map(function(i) { return i.words; }).join('').match(/\\d+/g)[0]) ||
                  (res.combined_text && res.combined_text.match(/\\d+/) && res.combined_text.match(/\\d+/)[0]);
            if (num) conf.textContent = '识别成功';
        }
        result.textContent = num || '?';
        if (!num) conf.textContent = '未识别到数字';
      })
      .catch(function(e) { result.textContent = 'Error'; conf.textContent = e.message; });
}

// ===== 图文朗读 =====
var ocrTtsUploadArea = document.getElementById('ocrTtsUploadArea');
ocrTtsUploadArea.addEventListener('click', function() { document.getElementById('ocrTtsImage').click(); });
ocrTtsUploadArea.addEventListener('dragover', function(e) { e.preventDefault(); ocrTtsUploadArea.classList.add('dragover'); });
ocrTtsUploadArea.addEventListener('dragleave', function() { ocrTtsUploadArea.classList.remove('dragover'); });
ocrTtsUploadArea.addEventListener('drop', function(e) {
    e.preventDefault();
    ocrTtsUploadArea.classList.remove('dragover');
    var files = e.dataTransfer.files;
    if (files && files[0]) {
        document.getElementById('ocrTtsImage').files = files;
        previewOcrTts(files[0]);
    }
});
document.getElementById('ocrTtsImage').addEventListener('change', function(e) {
    if (e.target.files && e.target.files[0]) previewOcrTts(e.target.files[0]);
});

function previewOcrTts(file) {
    var reader = new FileReader();
    reader.onload = function(e) {
        var img = document.getElementById('ocrTtsPreview');
        img.src = e.target.result;
        img.style.display = 'block';
    };
    reader.readAsDataURL(file);
}

function recognizeAndSpeak() {
    var input = document.getElementById('ocrTtsImage');
    var voice = document.getElementById('ocrTtsVoice').value;
    var status = document.getElementById('ocrTtsStatus');
    var resultDiv = document.getElementById('ocrTtsResult');
    var countDiv = document.getElementById('ocrTtsCharCount');
    var audio = document.getElementById('ocrTtsAudio');
    var btn = document.getElementById('ocrTtsBtn');

    if (!input.files || !input.files[0]) {
        alert('请先上传图片');
        return;
    }

    status.textContent = '识别中...';
    resultDiv.textContent = '';
    countDiv.textContent = '';
    audio.style.display = 'none';
    btn.disabled = true;

    var reader = new FileReader();
    reader.onload = function(e) {
        var b64 = e.target.result.split(',')[1];
        status.textContent = '识别并合成语音中...';

        fetch('/api/ocr-tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: b64, voice: voice })
        }).then(function(r) { return r.json(); })
          .then(function(res) {
            if (res.error) {
                status.textContent = '错误：' + res.error;
                return;
            }
            resultDiv.textContent = res.text || '（无文字内容）';
            countDiv.textContent = '共 ' + res.char_count + ' 个字符';
            status.textContent = '识别完成，正在播放...';

            if (res.audio) {
                audio.src = 'data:audio/mp3;base64,' + res.audio;
                audio.style.display = 'block';
                audio.play();
                audio.onended = function() { status.textContent = '朗读完成'; };
                audio.onerror = function() { status.textContent = '音频播放失败'; };
            }
          })
          .catch(function(err) {
            status.textContent = '请求失败：' + err.message;
          })
          .finally(function() {
            btn.disabled = false;
          });
    };
    reader.readAsDataURL(input.files[0]);
}

// ===== TTS =====
function synthesize() {
    var text = document.getElementById('ttsText').value;
    var voice = document.getElementById('ttsVoice').value;
    var status = document.getElementById('ttsStatus');
    var audio = document.getElementById('ttsAudio');
    var btn = document.getElementById('ttsBtn');
    if (!text) { alert('请输入文字'); return; }
    status.textContent = '合成中...';
    btn.disabled = true;

    fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text, voice: voice })
    }).then(function(r) { return r.json(); })
      .then(function(res) {
        if (res.error) { status.textContent = '错误：' + res.error; }
        else if (res.audio) {
            audio.src = 'data:audio/mp3;base64,' + res.audio;
            audio.style.display = 'block';
            audio.play();
            status.textContent = '播放中...';
        }
      })
      .catch(function(e) { status.textContent = 'Error: ' + e.message; })
      .finally(function() { btn.disabled = false; });
}

// ===== Config =====
function checkConfig() {
    fetch('/api/config').then(function(r) { return r.json(); })
      .then(function(cfg) {
        var bk = cfg.baidu && cfg.baidu.api_key && !cfg.baidu.api_key.includes('fill');
        var zk = cfg.zhipu && cfg.zhipu.api_key && !cfg.zhipu.api_key.includes('fill');
        var el = document.getElementById('config-status');
        if (bk && zk) el.innerHTML = '<div class="alert alert-success">所有 API Key 已配置，功能可正常使用</div>';
        else {
            var msg = '<div class="alert alert-warning">⚠️ ';
            if (!bk) msg += '百度 AI 未配置 | ';
            if (!zk) msg += '智谱 AI 未配置 | ';
            msg += '请前往【配置】页面设置</div>';
            el.innerHTML = msg;
        }
      }).catch(function() {});
}

function loadConfig() {
    fetch('/api/config').then(function(r) { return r.json(); })
      .then(function(cfg) {
        var baiduKey = document.getElementById('baiduApiKey');
        var baiduSec = document.getElementById('baiduSecret');
        var zhipuKey = document.getElementById('zhipuApiKey');
        if (baiduKey) baiduKey.value = cfg.baidu ? cfg.baidu.api_key : '';
        if (baiduSec) baiduSec.value = cfg.baidu ? cfg.baidu.secret_key : '';
        if (zhipuKey) zhipuKey.value = cfg.zhipu ? cfg.zhipu.api_key : '';
      }).catch(function(e) { console.error('加载配置失败:', e); });
}

function saveConfig() {
}

function saveConfig() {
    var cfg = {
        baidu: {
            api_key: document.getElementById('baiduApiKey').value,
            secret_key: document.getElementById('baiduSecret').value
        },
        zhipu: { api_key: document.getElementById('zhipuApiKey').value }
    };
    fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg)
    }).then(function(r) { return r.json(); })
      .then(function(res) {
        document.getElementById('configStatus').innerHTML = '<div class="alert alert-success">配置已保存！</div>';
        setTimeout(checkConfig, 1000);
      }).catch(function() {});
}

// Init
checkConfig();
loadConfig();
initVisionUpload();

// ===== 版本信息 =====
var versionDiv = document.createElement('div');
versionDiv.style.cssText = 'text-align:center;color:#555;font-size:11px;margin-top:30px;padding-bottom:15px';
versionDiv.textContent = '@AI 工具箱 v2.0.3 | @作者：AI wangmingting';
document.body.appendChild(versionDiv);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    sid = get_session_id()
    reply = zhipu_service.chat(sid, request.get_json().get("message", ""))
    resp = jsonify({"reply": reply})
    resp.set_cookie("session_id", sid, max_age=86400 * 30)
    return resp


@app.route("/api/chat/clear", methods=["POST"])
def api_chat_clear():
    zhipu_service.clear(get_session_id())
    return jsonify({"success": True})


@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    return jsonify(baidu_service.ocr(request.get_json().get("image", "")))


@app.route("/api/digit", methods=["POST"])
def api_digit():
    return jsonify(baidu_service.recognize_digits(request.get_json().get("image", "")))


@app.route("/api/tts", methods=["POST"])
def api_tts():
    d = request.get_json()
    return jsonify(baidu_service.tts(d.get("text", ""), d.get("voice", "1")))


@app.route("/api/ocr-tts", methods=["POST"])
def api_ocr_tts():
    """OCR 识别 + TTS 朗读（两步合一）"""
    data = request.get_json()
    img_b64 = data.get("image", "")
    voice = data.get("voice", "1")

    # 第一步：OCR 识别
    ocr_result = baidu_service.ocr(img_b64)

    if "error" in ocr_result:
        return jsonify({"error": ocr_result["error"], "step": "ocr"})

    # 提取文字
    if "words_result" in ocr_result and ocr_result["words_result"]:
        text = "".join(item.get("words", "") for item in ocr_result["words_result"] if item.get("words"))
    elif "combined_text" in ocr_result:
        text = ocr_result["combined_text"]
    else:
        return jsonify({"error": "未识别到文字内容", "step": "ocr", "raw": ocr_result})

    if not text or not text.strip():
        return jsonify({"error": "识别结果为空，请换一张更清晰的图片", "step": "ocr"})

    # 清理文本（去掉多余空白，只保留可读字符）
    text = re.sub(r"\s+", " ", text).strip()
    # 只保留中文、英文、数字、常用标点符号
    text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9，。、！？；：""''（）【】《》,.!?;:"\'\(\)\[\]<>-]', '', text)
    if not text or len(text) < 2:
        return jsonify({"error": "识别结果包含过多特殊字符，无法朗读", "step": "ocr"})

    # 第二步：TTS 合成
    tts_result = baidu_service.tts(text, voice)

    if "error" in tts_result:
        return jsonify({"error": tts_result["error"], "step": "tts", "text": text})

    return jsonify({
        "success": True,
        "text": text,
        "audio": tts_result.get("audio", ""),
        "char_count": len(text)
    })


@app.route("/api/vision", methods=["POST"])
def api_vision():
    """看图识物 - 通用图像识别"""
    data = request.get_json()
    img_b64 = data.get("image", "")
    if not img_b64:
        return jsonify({"error": "请上传图片"})
    result = baidu_service.vision(img_b64)
    if "error" in result:
        return jsonify({"error": result["error"]})
    return jsonify(result)


@app.route("/api/animal", methods=["POST"])
def api_animal():
    """动物识别"""
    data = request.get_json()
    img_b64 = data.get("image", "")
    if not img_b64:
        return jsonify({"error": "请上传图片"})
    result = baidu_service.animal(img_b64)
    if "error" in result:
        return jsonify({"error": result["error"]})
    return jsonify(result)


@app.route("/api/plant", methods=["POST"])
def api_plant():
    """植物识别"""
    data = request.get_json()
    img_b64 = data.get("image", "")
    if not img_b64:
        return jsonify({"error": "请上传图片"})
    result = baidu_service.plant(img_b64)
    if "error" in result:
        return jsonify({"error": result["error"]})
    return jsonify(result)


@app.route("/api/debug", methods=["GET"])
def api_debug():
    """诊断接口：检查配置和网络连通性"""
    cfg = load_config()
    bk = cfg.get("baidu", {})
    ak = bk.get("api_key", "")
    sk = bk.get("secret_key", "")
    
    result = {
        "config": {
            "baidu_api_key": ak[:8] + "..." if len(ak) > 8 else "(未配置)" if not ak else ak,
            "baidu_secret_key": sk[:8] + "..." if len(sk) > 8 else "(未配置)" if not sk else sk,
            "zhipu_api_key": cfg.get("zhipu", {}).get("api_key", "")[:8] + "..." if cfg.get("zhipu", {}).get("api_key") else "(未配置)"
        },
        "network": {},
        "token": None
    }
    
    # 测试百度 Token 获取
    if ak and sk and "请填写" not in ak and "请填写" not in sk:
        try:
            r = requests.post(
                BAIDU_TOKEN_URL,
                params={"grant_type": "client_credentials", "client_id": ak, "client_secret": sk},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15
            )
            result["network"]["baidu_token_status"] = r.status_code
            result["network"]["baidu_token_response_length"] = len(r.text)
            if r.text.strip():
                try:
                    tok_data = r.json()
                    if "access_token" in tok_data:
                        result["token"] = "获取成功 (长度: " + str(len(tok_data["access_token"])) + ")"
                    else:
                        result["token"] = "获取失败: " + tok_data.get("error_description", tok_data.get("error", "未知错误"))
                except:
                    result["token"] = "响应解析失败，原始内容: " + r.text[:200]
            else:
                result["token"] = "百度返回空响应"
        except Exception as e:
            result["token"] = "请求失败: " + str(e)
    else:
        result["token"] = "API Key 未配置"
    
    return jsonify(result)


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(load_config())
    save_config(request.get_json())
    return jsonify({"success": True})


if __name__ == "__main__":
    # ==================== 启动信息 ====================
    VERSION = "v2.0.0"
    AUTHOR = "AI wangmingting"
    print("=" * 60)
    print(f"  AI 工具箱 - 优化版 {VERSION}")
    print("=" * 60)
    bk, zk = check_api_keys()
    print(f"  百度 AI: {'✅ 已配置' if bk else '❌ 未配置'}")
    print(f"  智谱 AI: {'✅ 已配置' if zk else '❌ 未配置'}")
    if not bk or not zk:
        print("  ⚠️  部分 API Key 未配置，相关功能无法使用")
    print("-" * 60)
    print("  🌐 访问地址：http://localhost:5000")
    print("  📖 功能模块：智能客服 | OCR 识别 | 数字识别 | 图文朗读 | 语音合成")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)

# ==================== 文件信息 ====================
# 文件名：AI 工具箱_优化版.py
# 版本号：v2.0.0
# 作者：AI
# 描述：AI 智能助手平台 - 集智能客服、OCR 识别、数字识别、语音合成于一体
# 更新日志：
#   v2.0.0 - 2026-04-02
#     - 新增图文朗读功能（OCR + TTS 一键完成）
#     - 配置热更新（修改 API Key 后自动刷新 Token）
#     - 前端 XSS 防护（使用 textContent 替代 innerHTML）
#     - UUID 会话管理（避免多用户串话）
#     - 统一错误处理，优化用户体验
# ==================== END ====================
