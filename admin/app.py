"""Streamlit 管理界面：初始化向导 + 规则管理 + 消息日志。"""

import json
import sys
import uuid
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from admin.setup_wizard import (
    PROVIDER_DEFAULT_MODELS,
    PROVIDER_LABELS,
    check_accessibility_permission,
    check_wecom_running,
    ensure_fillers_file,
    ensure_rules_file,
    get_daemon_status,
    is_configured,
    read_current_config,
    set_env_key,
    start_daemon,
    stop_daemon,
    validate_api_key,
    write_env,
)
from storage.fillers import load_fillers, save_fillers

RULES_FILE = _ROOT / "rules.json"
FILLERS_FILE = _ROOT / "fillers.json"

st.set_page_config(page_title="企业微信自动回复 - 管理后台", layout="wide")
st.title("企业微信自动回复 管理后台")

tab_setup, tab_rules, tab_settings, tab_logs = st.tabs(
    ["⚙️ 初始化向导", "回复规则", "🛠 高级设置", "消息日志"]
)


# ══════════════════════════════════════════════════════════════
# Tab 1: 初始化向导
# ══════════════════════════════════════════════════════════════

def _mask_key(key: str) -> str:
    if len(key) > 8:
        return key[:6] + "..." + key[-4:]
    return "***" if key else "（未配置）"


def _render_daemon_controls():
    """守护进程启停控件，概览页和步骤 5 共用。"""
    running, status_msg = get_daemon_status()

    col_status, col_btn = st.columns([3, 1])
    with col_status:
        if running:
            st.success(f"自动回复守护进程：{status_msg}")
        else:
            st.warning(f"自动回复守护进程：{status_msg}")

    with col_btn:
        if running:
            if st.button("停止", type="secondary", key="daemon_stop"):
                ok2, msg2 = stop_daemon()
                st.toast(msg2, icon="⏹️" if ok2 else "⚠️")
                st.rerun()
        else:
            if st.button("启动", type="primary", key="daemon_start"):
                ok2, msg2 = start_daemon()
                if ok2:
                    st.toast(msg2, icon="✅")
                else:
                    st.error(msg2)
                st.rerun()


def _render_config_overview():
    """已完成初始配置后显示的只读概览面板。"""
    cfg = read_current_config()

    st.success("系统已完成初始化配置。")

    # ── 快捷开关 ──────────────────────────────────────────
    col_llm, col_filler = st.columns(2)
    with col_llm:
        llm_on = st.toggle(
            "启用大模型回复",
            value=cfg["LLM_ENABLED"],
            help="关闭后即使配置了 API Key 也不会调用 LLM",
        )
        if llm_on != cfg["LLM_ENABLED"]:
            set_env_key("LLM_ENABLED", str(llm_on).lower())
            st.rerun()
    with col_filler:
        filler_on = st.toggle(
            "启用废话库",
            value=cfg["FILLER_ENABLED"],
            help="无规则命中时从废话库随机抽取回复",
        )
        if filler_on != cfg["FILLER_ENABLED"]:
            set_env_key("FILLER_ENABLED", str(filler_on).lower())
            st.rerun()

    st.divider()

    # ── 配置概览 ──────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    provider_label = PROVIDER_LABELS.get(cfg["LLM_PROVIDER"], cfg["LLM_PROVIDER"])
    col1.metric("大模型 Provider", provider_label if cfg["LLM_ENABLED"] else "未启用")
    col2.metric("模型", cfg["LLM_MODEL"] or PROVIDER_DEFAULT_MODELS.get(cfg["LLM_PROVIDER"], "-"))
    col3.metric(
        "回复延迟",
        f"{cfg['REPLY_DELAY_MIN_SECONDS']:.1f}–{cfg['REPLY_DELAY_MAX_SECONDS']:.1f} 秒",
    )
    col4.metric("轮询间隔", f"{cfg['POLL_INTERVAL_SECONDS']} 秒")

    if cfg["LLM_API_KEY"]:
        st.caption(f"API Key：{_mask_key(cfg['LLM_API_KEY'])}")

    # ── 守护进程状态 ──────────────────────────────────────
    st.divider()
    _render_daemon_controls()

    # ── 企业微信运行状态 ──────────────────────────────────
    wc_ok, wc_msg = check_wecom_running(cfg["WECOM_BUNDLE_ID"])
    if wc_ok:
        st.success(f"企业微信：{wc_msg}")
    else:
        st.warning(f"企业微信：{wc_msg}")

    st.divider()
    if st.button("重新配置向导", type="secondary"):
        for k in list(st.session_state.keys()):
            if k.startswith("wiz_"):
                del st.session_state[k]
        st.session_state["wiz_force"] = True
        st.rerun()


# ── 向导步骤渲染 ──────────────────────────────────────────────

WIZARD_STEPS = ["大模型 API", "AI 配置", "回复行为", "初始规则", "确认写入"]


def _wizard_progress(step: int):
    st.progress((step - 1) / len(WIZARD_STEPS), text=f"步骤 {step} / {len(WIZARD_STEPS)}：{WIZARD_STEPS[step - 1]}")
    cols = st.columns(len(WIZARD_STEPS))
    for i, label in enumerate(WIZARD_STEPS, start=1):
        prefix = "✅" if i < step else ("▶" if i == step else "○")
        cols[i - 1].caption(f"{prefix} {i}. {label}")
    st.divider()


def _wizard_step1():
    _wizard_progress(1)
    st.subheader("步骤 1：大模型 API（可跳过）")

    llm_on = st.checkbox(
        "启用大模型回复",
        value=st.session_state.get("wiz_llm_enabled", False),
        key="wiz_llm_enabled_cb",
        help="不启用时将使用纯规则 + 废话库模式",
    )
    st.session_state["wiz_llm_enabled"] = llm_on

    if llm_on:
        provider_keys = list(PROVIDER_LABELS.keys())
        provider_labels = list(PROVIDER_LABELS.values())
        cur_provider = st.session_state.get("wiz_provider", "anthropic")
        cur_idx = provider_keys.index(cur_provider) if cur_provider in provider_keys else 0
        selected_label = st.selectbox(
            "大模型 Provider", provider_labels, index=cur_idx, key="wiz_provider_sel"
        )
        provider = provider_keys[provider_labels.index(selected_label)]
        st.session_state["wiz_provider"] = provider

        if provider == "custom":
            base_url = st.text_input(
                "Base URL（OpenAI 兼容接口地址）",
                value=st.session_state.get("wiz_base_url", ""),
                placeholder="https://your-api.example.com/v1",
                key="wiz_base_url_input",
            )
            st.session_state["wiz_base_url"] = base_url
        else:
            st.session_state["wiz_base_url"] = ""

        api_key = st.text_input(
            "API Key",
            value=st.session_state.get("wiz_api_key", ""),
            type="password",
            placeholder="输入 API Key...",
            key="wiz_api_key_input",
        )
        st.session_state["wiz_api_key"] = api_key

        if st.button("验证 API Key", type="primary", key="wiz_verify_btn"):
            if not api_key.strip():
                st.error("请先输入 API Key")
            else:
                with st.spinner("正在验证..."):
                    ok, msg = validate_api_key(
                        provider, api_key,
                        st.session_state.get("wiz_base_url", ""),
                    )
                if ok:
                    st.success(msg)
                    st.session_state["wiz_step1_verified"] = True
                else:
                    st.error(msg)
                    st.session_state["wiz_step1_verified"] = False

        if st.session_state.get("wiz_step1_verified"):
            st.caption("API Key 已验证")

    col_next, _ = st.columns([1, 4])
    with col_next:
        can_proceed = (not llm_on) or st.session_state.get("wiz_step1_verified", False)
        if st.button("下一步 →", type="primary", key="wiz_s1_next", disabled=not can_proceed):
            st.session_state["wiz_step"] = 2
            st.rerun()


def _wizard_step2():
    _wizard_progress(2)
    provider = st.session_state.get("wiz_provider", "anthropic")
    llm_on = st.session_state.get("wiz_llm_enabled", False)

    if not llm_on:
        st.info("大模型未启用，跳过 AI 配置。")
        col_back, col_next, _ = st.columns([1, 1, 4])
        with col_back:
            if st.button("← 上一步", key="wiz_s2_back"):
                st.session_state["wiz_step"] = 1
                st.rerun()
        with col_next:
            if st.button("下一步 →", type="primary", key="wiz_s2_next_skip"):
                st.session_state["wiz_step"] = 3
                st.rerun()
        return

    st.subheader("步骤 2：AI 配置")

    default_model = PROVIDER_DEFAULT_MODELS.get(provider, "")
    model = st.text_input(
        "模型名称",
        value=st.session_state.get("wiz_model", default_model),
        placeholder=f"默认：{default_model}",
        key="wiz_model_input",
    )
    st.session_state["wiz_model"] = model

    system_prompt = st.text_area(
        "System Prompt（AI 角色描述）",
        value=st.session_state.get("wiz_system_prompt", "你是一位专业的客服助手，请用简洁、礼貌的中文回复客户问题。"),
        height=120,
        key="wiz_prompt_input",
    )
    st.session_state["wiz_system_prompt"] = system_prompt

    col_back, col_next, _ = st.columns([1, 1, 4])
    with col_back:
        if st.button("← 上一步", key="wiz_s2_back"):
            st.session_state["wiz_step"] = 1
            st.rerun()
    with col_next:
        if st.button("下一步 →", type="primary", key="wiz_s2_next"):
            st.session_state["wiz_step"] = 3
            st.rerun()


def _wizard_step3():
    _wizard_progress(3)
    st.subheader("步骤 3：回复行为")

    cfg = read_current_config()

    poll = st.slider(
        "轮询间隔（秒）：检查企业微信未读消息的频率",
        min_value=1, max_value=60,
        value=st.session_state.get("wiz_poll", cfg["POLL_INTERVAL_SECONDS"]),
        key="wiz_poll_slider",
    )
    st.session_state["wiz_poll"] = poll

    st.markdown("**随机回复延迟（模拟人工回复节奏）**")
    col_min, col_max = st.columns(2)
    delay_min = col_min.number_input(
        "最短延迟（秒）",
        min_value=0.0, max_value=120.0, step=0.5,
        value=st.session_state.get("wiz_delay_min", cfg["REPLY_DELAY_MIN_SECONDS"]),
        key="wiz_delay_min_input",
    )
    delay_max = col_max.number_input(
        "最长延迟（秒）",
        min_value=0.0, max_value=120.0, step=0.5,
        value=st.session_state.get("wiz_delay_max", cfg["REPLY_DELAY_MAX_SECONDS"]),
        key="wiz_delay_max_input",
    )
    if delay_min > delay_max:
        st.warning("最短延迟不能大于最长延迟")
    st.session_state["wiz_delay_min"] = delay_min
    st.session_state["wiz_delay_max"] = delay_max

    st.divider()
    filler_on = st.checkbox(
        "启用废话库（无规则命中时随机发送一条废话）",
        value=st.session_state.get("wiz_filler_enabled", False),
        key="wiz_filler_cb",
    )
    st.session_state["wiz_filler_enabled"] = filler_on

    if filler_on:
        existing = "\n".join(st.session_state.get("wiz_fillers", []))
        filler_text = st.text_area(
            "废话库（每行一条）",
            value=existing,
            height=150,
            placeholder="好的\n收到～\n稍等一下\n了解，我帮您查一下",
            key="wiz_filler_textarea",
        )
        st.session_state["wiz_fillers"] = [
            line.strip() for line in filler_text.splitlines() if line.strip()
        ]

    st.divider()
    log_level = st.selectbox(
        "日志级别",
        ["DEBUG", "INFO", "WARNING", "ERROR"],
        index=["DEBUG", "INFO", "WARNING", "ERROR"].index(
            st.session_state.get("wiz_log_level", cfg["LOG_LEVEL"])
        ),
        key="wiz_log_level_sel",
    )
    st.session_state["wiz_log_level"] = log_level

    col_back, col_next, _ = st.columns([1, 1, 4])
    with col_back:
        if st.button("← 上一步", key="wiz_s3_back"):
            st.session_state["wiz_step"] = 2
            st.rerun()
    with col_next:
        if st.button("下一步 →", type="primary", key="wiz_s3_next", disabled=(delay_min > delay_max)):
            st.session_state["wiz_step"] = 4
            st.rerun()


def _wizard_step4():
    _wizard_progress(4)
    st.subheader("步骤 4：初始回复规则（可跳过）")
    st.caption("在这里添加 1-3 条常用关键词规则，之后可在「回复规则」Tab 中继续管理。")

    if "wiz_init_rules" not in st.session_state:
        st.session_state["wiz_init_rules"] = []

    rules_list = st.session_state["wiz_init_rules"]

    for i, rule in enumerate(rules_list):
        with st.expander(f"规则 {i + 1}：{rule.get('name', '未命名')}", expanded=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                rules_list[i]["name"] = st.text_input("规则名称", value=rule.get("name", ""), key=f"init_name_{i}")
                rules_list[i]["keyword"] = st.text_input("关键词（包含匹配）", value=rule.get("keyword", ""), key=f"init_kw_{i}")
                rules_list[i]["reply"] = st.text_area("回复内容", value=rule.get("reply", ""), key=f"init_reply_{i}", height=80)
            with col2:
                if st.button("删除", key=f"init_del_{i}", type="secondary"):
                    rules_list.pop(i)
                    st.session_state["wiz_init_rules"] = rules_list
                    st.rerun()

    if len(rules_list) < 5:
        if st.button("+ 添加规则", key="wiz_add_rule"):
            rules_list.append({"name": "", "keyword": "", "reply": ""})
            st.session_state["wiz_init_rules"] = rules_list
            st.rerun()

    col_back, col_next, col_skip = st.columns([1, 1, 2])
    with col_back:
        if st.button("← 上一步", key="wiz_s4_back"):
            st.session_state["wiz_step"] = 3
            st.rerun()
    with col_next:
        if st.button("下一步 →", type="primary", key="wiz_s4_next"):
            st.session_state["wiz_step"] = 5
            st.rerun()
    with col_skip:
        if st.button("跳过（不添加规则）→", key="wiz_s4_skip"):
            st.session_state["wiz_init_rules"] = []
            st.session_state["wiz_step"] = 5
            st.rerun()


def _wizard_step5():
    _wizard_progress(5)
    st.subheader("步骤 5：确认并写入配置")

    llm_on = st.session_state.get("wiz_llm_enabled", False)
    provider = st.session_state.get("wiz_provider", "anthropic")
    api_key = st.session_state.get("wiz_api_key", "")
    base_url = st.session_state.get("wiz_base_url", "")
    model = st.session_state.get("wiz_model", PROVIDER_DEFAULT_MODELS.get(provider, ""))
    system_prompt = st.session_state.get("wiz_system_prompt", "你是一位专业的客服助手，请用简洁、礼貌的中文回复客户问题。")
    poll = st.session_state.get("wiz_poll", 5)
    delay_min = st.session_state.get("wiz_delay_min", 1.0)
    delay_max = st.session_state.get("wiz_delay_max", 5.0)
    filler_on = st.session_state.get("wiz_filler_enabled", False)
    fillers = st.session_state.get("wiz_fillers", [])
    log_level = st.session_state.get("wiz_log_level", "INFO")
    init_rules = st.session_state.get("wiz_init_rules", [])

    st.markdown("**即将写入的配置：**")
    lines = [
        f"大模型：{'启用 — ' + PROVIDER_LABELS.get(provider, provider) if llm_on else '未启用'}",
        f"模型：{model or '（使用 provider 默认值）'}" if llm_on else "",
        f"API Key：{_mask_key(api_key)}" if llm_on and api_key else "",
        f"废话库：{'启用，{} 条'.format(len(fillers)) if filler_on else '未启用'}",
        f"轮询间隔：{poll} 秒",
        f"回复延迟：{delay_min:.1f}–{delay_max:.1f} 秒",
        f"初始规则：{len([r for r in init_rules if r.get('keyword')])} 条",
        f"日志级别：{log_level}",
    ]
    st.code("\n".join(l for l in lines if l), language="text")

    col_back, col_confirm, _ = st.columns([1, 2, 3])
    with col_back:
        if st.button("← 上一步", key="wiz_s5_back"):
            st.session_state["wiz_step"] = 4
            st.rerun()
    with col_confirm:
        if st.button("确认写入配置文件", type="primary", key="wiz_confirm"):
            config = {
                "LLM_PROVIDER": provider,
                "LLM_API_KEY": api_key,
                "LLM_BASE_URL": base_url,
                "LLM_MODEL": model,
                "LLM_ENABLED": str(llm_on).lower(),
                "SYSTEM_PROMPT": system_prompt,
                "FILLER_ENABLED": str(filler_on).lower(),
                "REPLY_DELAY_MIN_SECONDS": str(delay_min),
                "REPLY_DELAY_MAX_SECONDS": str(delay_max),
                "POLL_INTERVAL_SECONDS": str(poll),
                "LOG_LEVEL": log_level,
                "DATABASE_URL": "sqlite:///./messages.db",
                "WECOM_BUNDLE_ID": "com.tencent.WeWorkMac",
                "WIZARD_DONE": "true",
            }
            try:
                write_env(config)
                # 初始规则
                valid_rules = [
                    {
                        "id": f"rule_{uuid.uuid4().hex[:6]}",
                        "enabled": True,
                        "name": r.get("name", ""),
                        "match_type": "contains",
                        "keyword": r.get("keyword", ""),
                        "pattern": None,
                        "reply": r.get("reply", ""),
                        "priority": idx + 1,
                    }
                    for idx, r in enumerate(init_rules)
                    if r.get("keyword") and r.get("reply")
                ]
                ensure_rules_file(valid_rules if valid_rules else None)
                # 废话库
                if filler_on and fillers:
                    from storage.fillers import save_fillers as _sf
                    _sf(fillers)
                else:
                    ensure_fillers_file()

                st.success("配置文件已写入！")
                st.balloons()

                st.divider()
                st.markdown("### 启动自动回复守护进程")
                _render_daemon_controls()

                if st.button("完成，查看配置概览", type="primary", key="wiz_done"):
                    for k in list(st.session_state.keys()):
                        if k.startswith("wiz_"):
                            del st.session_state[k]
                    st.rerun()
            except Exception as e:
                st.error(f"写入失败：{e}")


def _render_wizard():
    step = st.session_state.get("wiz_step", 1)
    if step == 1:
        _wizard_step1()
    elif step == 2:
        _wizard_step2()
    elif step == 3:
        _wizard_step3()
    elif step == 4:
        _wizard_step4()
    elif step == 5:
        _wizard_step5()


with tab_setup:
    if is_configured() and not st.session_state.get("wiz_force", False):
        _render_config_overview()
    else:
        _render_wizard()


# ══════════════════════════════════════════════════════════════
# Tab 2: 回复规则
# ══════════════════════════════════════════════════════════════

def _load_rules() -> dict:
    with open(RULES_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_rules(data: dict) -> None:
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


with tab_rules:
    ensure_rules_file()

    # ── 规则测试器 ──────────────────────────────────────────
    with st.expander("🧪 规则测试器（不发送，仅预览命中情况）", expanded=False):
        st.caption(
            "输入一条模拟客户消息，查看会命中哪条规则 / 废话库 / LLM。"
            "正则匹配段会用高亮显示。"
        )
        test_input = st.text_area("模拟客户消息", height=80, key="rule_test_input")
        if st.button("运行测试", key="rule_test_run"):
            import re
            _test_rules_data = _load_rules()
            _test_rules = [
                r for r in _test_rules_data.get("rules", [])
                if r.get("enabled", True)
            ]
            _test_rules.sort(key=lambda r: r.get("priority", 99))

            hit = None
            highlighted = test_input
            for r in _test_rules:
                mt = r.get("match_type", "contains")
                ic = r.get("ignore_case", False)
                if mt == "exact":
                    kw = (r.get("keyword") or "").strip()
                    if kw and test_input.strip() == kw:
                        hit = ("rules", r, kw)
                        highlighted = f"<mark style='background:#fef08a'>{test_input}</mark>"
                        break
                elif mt == "contains":
                    kw = (r.get("keyword") or "").strip()
                    hit_ok = (
                        (ic and kw.lower() in test_input.lower())
                        or (not ic and kw and kw in test_input)
                    )
                    if hit_ok:
                        hit = ("rules", r, kw)
                        # 替换原文中的匹配段（简化处理：先按 ic 找位置）
                        haystack = test_input
                        needle_lo = kw.lower() if ic else kw
                        src_lo = haystack.lower() if ic else haystack
                        idx = src_lo.find(needle_lo)
                        if idx >= 0:
                            seg = haystack[idx:idx + len(kw)]
                            highlighted = (
                                haystack[:idx]
                                + f"<mark style='background:#fef08a'>{seg}</mark>"
                                + haystack[idx + len(kw):]
                            )
                        break
                elif mt == "regex":
                    pat = r.get("pattern") or ""
                    if not pat:
                        continue
                    flags = re.MULTILINE | (re.IGNORECASE if ic else 0)
                    try:
                        m = re.search(pat, test_input, flags=flags)
                    except re.error as e:
                        st.error(f"规则 `{r.get('name')}` 正则错误：{e}")
                        continue
                    if m:
                        hit = ("rules", r, m.group(0))
                        highlighted = (
                            test_input[:m.start()]
                            + f"<mark style='background:#fef08a'>{m.group(0)}</mark>"
                            + test_input[m.end():]
                        )
                        break

            if hit:
                _, rule_obj, matched = hit
                st.success(
                    f"✅ 命中规则：**{rule_obj.get('name')}** "
                    f"(match_type={rule_obj.get('match_type')}, 匹配段=`{matched}`)"
                )
                st.markdown("**消息（高亮匹配段）**：", unsafe_allow_html=True)
                st.markdown(highlighted, unsafe_allow_html=True)
                st.markdown(f"**将回复**：`{rule_obj.get('reply')}`")
            else:
                from config import Settings as _S
                _scur = _S()
                if _scur.filler_enabled:
                    st.info("🟡 无规则命中 → 将走**废话库**随机")
                elif _scur.llm_enabled and _scur.effective_api_key:
                    st.info("🔵 无规则命中 → 将走 **LLM**")
                else:
                    st.warning("⚪ 无规则命中 → 将静默不回复")

    st.divider()
    st.subheader("回复规则配置")

    rules_data = _load_rules()
    rules = rules_data.get("rules", [])

    for i, rule in enumerate(rules):
        with st.expander(
            f"{'✅' if rule.get('enabled') else '❌'} [{rule.get('match_type', '?')}] "
            f"{rule.get('name', rule.get('id', i))}",
            expanded=False,
        ):
            col1, col2 = st.columns([3, 1])
            with col1:
                new_name = st.text_input("规则名称", value=rule.get("name", ""), key=f"name_{i}")
                new_match = st.selectbox(
                    "匹配方式",
                    ["exact", "contains", "regex"],
                    index=["exact", "contains", "regex"].index(rule.get("match_type", "contains")),
                    key=f"match_{i}",
                )
                if new_match in ("exact", "contains"):
                    new_kw = st.text_input("关键词", value=rule.get("keyword") or "", key=f"kw_{i}")
                    new_pat = None
                else:
                    new_kw = None
                    new_pat = st.text_input("正则表达式", value=rule.get("pattern") or "", key=f"pat_{i}")
                    with st.expander("📖 正则语法速查", expanded=False):
                        st.markdown(
                            "- `.` 任意字符　`.*` 任意多个\n"
                            "- `^abc` 开头　`abc$` 结尾\n"
                            "- `\\d` 数字　`\\w` 字母/数字/下划线　`\\s` 空白\n"
                            "- `[甲乙丙]` 字符类　`[^...]` 取反\n"
                            "- `(?:a|b)` 或分支　`a{2,5}` 重复 2-5 次\n"
                            "- 示例：`价格|报价|多少钱` 一次命中三个关键词"
                        )
                new_reply = st.text_area("回复内容", value=rule.get("reply", ""), key=f"reply_{i}")
                new_priority = st.number_input("优先级（越小越高）", value=rule.get("priority", 99), step=1, key=f"pri_{i}")
                new_ignore_case = st.checkbox(
                    "忽略大小写",
                    value=rule.get("ignore_case", False),
                    key=f"ic_{i}",
                    help="对 contains / regex 都生效；exact 不受影响",
                )
            with col2:
                new_enabled = st.checkbox("启用", value=rule.get("enabled", True), key=f"en_{i}")
                if st.button("保存", key=f"save_{i}"):
                    rules[i].update(
                        name=new_name,
                        match_type=new_match,
                        keyword=new_kw,
                        pattern=new_pat,
                        reply=new_reply,
                        priority=int(new_priority),
                        enabled=new_enabled,
                        ignore_case=bool(new_ignore_case),
                    )
                    rules_data["rules"] = rules
                    _save_rules(rules_data)
                    st.success("已保存")
                if st.button("删除", key=f"del_{i}", type="secondary"):
                    rules.pop(i)
                    rules_data["rules"] = rules
                    _save_rules(rules_data)
                    st.rerun()

    st.divider()
    st.subheader("新增规则")
    with st.form("new_rule"):
        c1, c2 = st.columns(2)
        n_name = c1.text_input("规则名称")
        n_match = c2.selectbox("匹配方式", ["exact", "contains", "regex"])
        n_kw = st.text_input("关键词 / 正则表达式")
        if n_match == "regex":
            with st.expander("📖 正则语法速查", expanded=True):
                st.markdown(
                    "- `.` 任意字符　`.*` 任意多个\n"
                    "- `^abc` 开头　`abc$` 结尾\n"
                    "- `\\d` 数字　`\\w` 字母/数字/下划线　`\\s` 空白\n"
                    "- `[甲乙丙]` 字符类　`[^...]` 取反\n"
                    "- `(?:a|b)` 或分支　`a{2,5}` 重复 2-5 次\n"
                    "- 示例：`价格|报价|多少钱` 一次命中三个关键词"
                )
        n_reply = st.text_area("回复内容")
        n_priority = st.number_input("优先级", value=len(rules) + 1, step=1)
        n_ignore_case = st.checkbox("忽略大小写", value=False)
        if st.form_submit_button("添加规则"):
            new_rule = {
                "id": f"rule_{uuid.uuid4().hex[:6]}",
                "enabled": True,
                "name": n_name,
                "match_type": n_match,
                "keyword": n_kw if n_match != "regex" else None,
                "pattern": n_kw if n_match == "regex" else None,
                "reply": n_reply,
                "priority": int(n_priority),
                "ignore_case": bool(n_ignore_case),
            }
            rules.append(new_rule)
            rules_data["rules"] = rules
            _save_rules(rules_data)
            st.success("规则已添加")
            st.rerun()

    # ── 废话库管理 ──────────────────────────────────────────
    st.divider()
    st.subheader("废话库")
    st.caption("无规则命中时，若启用废话库，将从中随机不重复地抽取一条发送。")

    ensure_fillers_file()
    fillers_list = load_fillers()
    fillers_text = st.text_area(
        f"废话内容（每行一条，当前 {len(fillers_list)} 条）",
        value="\n".join(fillers_list),
        height=200,
        placeholder="好的\n收到～\n稍等一下\n了解，我帮您查一下",
        key="fillers_textarea",
    )
    col_save_f, col_toggle_f = st.columns([1, 3])
    with col_save_f:
        if st.button("保存废话库", type="primary", key="save_fillers_btn"):
            new_fillers = [line.strip() for line in fillers_text.splitlines() if line.strip()]
            save_fillers(new_fillers)
            st.success(f"已保存 {len(new_fillers)} 条废话")
            st.rerun()


# ══════════════════════════════════════════════════════════════
# Tab 3: 消息日志
# ══════════════════════════════════════════════════════════════

with tab_settings:
    # ── 守护进程控制 ──────────────────────────────────────
    st.subheader("守护进程")
    _running, _info = get_daemon_status()
    c_status, c_start, c_stop = st.columns([3, 1, 1])
    with c_status:
        if _running:
            st.success(f"✅ 运行中 — {_info}")
        else:
            st.warning(f"⏸ 未运行 — {_info}")
    with c_start:
        if st.button("▶ 启动", disabled=_running, key="set_start_daemon"):
            ok, msg = start_daemon()
            (st.success if ok else st.error)(msg)
            st.rerun()
    with c_stop:
        if st.button("⏹ 停止", disabled=not _running, key="set_stop_daemon"):
            ok, msg = stop_daemon()
            (st.success if ok else st.error)(msg)
            st.rerun()

    st.divider()
    st.subheader("运行参数")
    st.caption("保存后需要重启守护进程才能生效。")

    from config import Settings
    _cur = Settings()

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 发送行为")
        silent_send = st.toggle(
            "静默发送（不抢焦点）",
            value=_cur.silent_send,
            help="开启后发送回复时不再把企业微信拉到前台。实测少数情况下可能失败，"
                 "失败会在日志里显示；不确定时先保持关闭。",
        )
        delay_min = st.number_input(
            "随机回复延迟 - 最小（秒）",
            min_value=0.0, max_value=30.0, step=0.5,
            value=float(_cur.reply_delay_min_seconds),
        )
        delay_max = st.number_input(
            "随机回复延迟 - 最大（秒）",
            min_value=0.0, max_value=60.0, step=0.5,
            value=float(_cur.reply_delay_max_seconds),
            help="实际延迟会在 min~max 之间随机；过短容易被识破。",
        )
        poll_interval = st.number_input(
            "轮询间隔（秒）",
            min_value=1, max_value=60, step=1,
            value=int(_cur.poll_interval_seconds),
            help="守护进程多久检查一次未读消息。数字越小响应越快但 CPU 越高。",
        )
        group_chat_reply = st.toggle(
            "启用群聊自动回复",
            value=_cur.group_chat_reply,
            help="默认关闭：群名含 `、` 的会话被识别为群聊，跳过自动回复。",
        )

    with col2:
        st.markdown("#### 回复策略")
        filler_enabled = st.toggle(
            "启用废话库兜底",
            value=_cur.filler_enabled,
            help="规则未命中时从 fillers.json 随机抽一句回复。关闭后会走 LLM 或不回复。",
        )
        llm_enabled = st.toggle(
            "启用 LLM（大模型）",
            value=_cur.llm_enabled,
            help="规则和废话库都没命中时调用大模型。需要先在初始化向导配置 API Key。",
        )
        system_prompt = st.text_area(
            "大模型 system prompt",
            value=_cur.system_prompt,
            height=100,
        )
        llm_rate = st.number_input(
            "LLM 每客户每分钟最多调用次数",
            min_value=0, max_value=60, step=1,
            value=int(_cur.llm_rate_limit_per_minute),
            help="超限时改走废话库兜底；设为 0 表示不限流。",
        )

        st.markdown("#### 排除名单")
        # 从历史日志收集候选客户，方便多选
        try:
            from storage import message_log as _mlog
            _mlog.init_db()
            _hist = _mlog.get_recent_logs(limit=2000)
            _hist_senders = sorted({lg.customer_id for lg in _hist if lg.customer_id})
        except Exception:
            _hist_senders = []

        _current_excluded = [s.strip() for s in _cur.excluded_senders.split(",") if s.strip()]
        # 把当前值也加入候选集合
        _options = sorted(set(_hist_senders) | set(_current_excluded))
        excluded_multi = st.multiselect(
            "选择不回复的会话（从历史记录 + 当前名单中挑）",
            options=_options,
            default=[s for s in _current_excluded if s in _options],
            help="从下拉框多选；若需要输入新关键词，请用下方文本框补充。",
        )
        excluded_extra = st.text_input(
            "额外关键词（逗号分隔，子串匹配）",
            value=",".join(s for s in _current_excluded if s not in _options),
            help="例：刘,经营 — 匹配所有含『刘』或『经营』的会话",
        )
        _extra_list = [s.strip() for s in excluded_extra.split(",") if s.strip()]
        excluded_senders = ",".join(excluded_multi + _extra_list)

    st.divider()
    if st.button("💾 保存设置", type="primary"):
        # 校验
        errs = []
        if delay_min > delay_max:
            errs.append("最小延迟不能大于最大延迟")
        if errs:
            for e in errs:
                st.error(e)
        else:
            write_env({
                "SILENT_SEND": "true" if silent_send else "false",
                "REPLY_DELAY_MIN_SECONDS": f"{delay_min}",
                "REPLY_DELAY_MAX_SECONDS": f"{delay_max}",
                "POLL_INTERVAL_SECONDS": f"{int(poll_interval)}",
                "GROUP_CHAT_REPLY": "true" if group_chat_reply else "false",
                "FILLER_ENABLED": "true" if filler_enabled else "false",
                "LLM_ENABLED": "true" if llm_enabled else "false",
                "LLM_RATE_LIMIT_PER_MINUTE": f"{int(llm_rate)}",
                "SYSTEM_PROMPT": system_prompt,
                "EXCLUDED_SENDERS": excluded_senders,
            })
            st.success("已保存。请重启守护进程使设置生效。")

    with st.expander("当前生效配置（只读）"):
        st.json({
            "silent_send": _cur.silent_send,
            "reply_delay_min_seconds": _cur.reply_delay_min_seconds,
            "reply_delay_max_seconds": _cur.reply_delay_max_seconds,
            "poll_interval_seconds": _cur.poll_interval_seconds,
            "filler_enabled": _cur.filler_enabled,
            "llm_enabled": _cur.llm_enabled,
            "excluded_senders": _cur.excluded_senders,
            "wecom_bundle_id": _cur.wecom_bundle_id,
        })


with tab_logs:
    st.subheader("消息日志")

    try:
        from storage import message_log
        message_log.init_db()

        # 筛选控件
        fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 1])
        with fc1:
            q_customer = st.text_input("按客户筛选（子串）", key="log_f_customer")
        with fc2:
            q_source = st.multiselect(
                "来源",
                ["rules", "filler", "filler_ratelimited", "claude"],
                key="log_f_source",
            )
        with fc3:
            q_text = st.text_input("消息内容含", key="log_f_text")
        with fc4:
            q_limit = st.number_input("条数", min_value=50, max_value=2000, value=200, step=50)

        logs = message_log.get_recent_logs(limit=int(q_limit))

        # 应用筛选
        filtered = [
            lg for lg in logs
            if (not q_customer or q_customer.lower() in (lg.customer_id or "").lower())
            and (not q_source or lg.source in q_source)
            and (not q_text or q_text.lower() in (lg.message or "").lower())
        ]

        if not filtered:
            st.info(f"当前筛选条件下无记录（共扫描 {len(logs)} 条）。")
        else:
            import pandas as pd

            def fmt_latency(ms: int) -> str:
                if not ms or ms <= 0:
                    return "-"
                secs = ms / 1000
                if secs < 60:
                    return f"{secs:.1f}s"
                m, s = divmod(int(round(secs)), 60)
                return f"{m}m{s:02d}s"

            from datetime import timezone, timedelta
            CN_TZ = timezone(timedelta(hours=8))

            def fmt_time(dt):
                # DB 里按 UTC 存；标注为 UTC 后转北京时间
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")

            df = pd.DataFrame(
                [
                    {
                        "id": log.id,
                        "时间": fmt_time(log.created_at),
                        "客户": log.customer_id,
                        "消息": log.message,
                        "回复": log.reply,
                        "来源": log.source,
                        "发送方式": getattr(log, "send_method", "") or "-",
                        "耗时": fmt_latency(getattr(log, "latency_ms", 0) or 0),
                    }
                    for log in filtered
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("筛选后", f"{len(filtered)}/{len(logs)}")
            rules_count = sum(1 for lg in filtered if lg.source == "rules")
            filler_count = sum(1 for lg in filtered if lg.source and lg.source.startswith("filler"))
            claude_count = sum(1 for lg in filtered if lg.source == "claude")
            col2.metric("规则命中", rules_count)
            col3.metric("废话库", filler_count)
            col4.metric("LLM 兜底", claude_count)

            latencies = [getattr(lg, "latency_ms", 0) or 0 for lg in filtered]
            latencies = [x for x in latencies if x > 0]
            if latencies:
                avg_ms = sum(latencies) // len(latencies)
                col5.metric("平均耗时", fmt_latency(avg_ms))
            else:
                col5.metric("平均耗时", "-")

            # 发送方式分布
            from collections import Counter
            methods = Counter(
                getattr(lg, "send_method", "") or "-" for lg in filtered
            )
            if methods:
                st.caption("发送方式分布：" + "  ·  ".join(
                    f"`{k}` × {v}" for k, v in methods.most_common()
                ))

            # ── 统计图表 ──────────────────────────────────────
            with st.expander("📊 统计图表"):
                chart_df = pd.DataFrame([
                    {
                        "时": fmt_time(lg.created_at)[:13],  # 精度到小时
                        "来源": lg.source or "-",
                        "耗时(秒)": (getattr(lg, "latency_ms", 0) or 0) / 1000,
                    }
                    for lg in filtered
                ])
                if not chart_df.empty:
                    hourly = chart_df.groupby(["时", "来源"]).size().unstack(fill_value=0)
                    st.caption("按小时 × 来源的消息数")
                    st.bar_chart(hourly)

                    nonzero = chart_df[chart_df["耗时(秒)"] > 0]["耗时(秒)"]
                    if len(nonzero) > 1:
                        st.caption("响应耗时分布（秒）")
                        bins = pd.cut(nonzero, bins=[0, 5, 10, 20, 30, 60, 300, 99999],
                                      labels=["<5s", "5-10s", "10-20s", "20-30s", "30-60s", "1-5m", ">5m"])
                        st.bar_chart(bins.value_counts().sort_index())

            # ── 删除选中记录 ──────────────────────────────────
            with st.expander("🗑 删除记录"):
                del_ids_input = st.text_input(
                    "输入要删除的 id（逗号分隔，从上表 id 列取）",
                    key="log_del_ids",
                )
                cd1, cd2 = st.columns([1, 3])
                with cd1:
                    if st.button("删除", key="log_del_btn"):
                        try:
                            ids = [int(x.strip()) for x in del_ids_input.split(",") if x.strip()]
                        except ValueError:
                            st.error("id 必须是数字")
                            ids = []
                        if ids:
                            n = message_log.delete_by_ids(ids)
                            st.success(f"已删除 {n} 条")
                            st.rerun()
                with cd2:
                    if st.button("⚠️ 清空全部日志", key="log_clear_all", type="secondary"):
                        if st.session_state.get("confirm_clear_all"):
                            n = message_log.delete_all()
                            st.success(f"已清空 {n} 条")
                            st.session_state["confirm_clear_all"] = False
                            st.rerun()
                        else:
                            st.session_state["confirm_clear_all"] = True
                            st.warning("再点一次确认清空全部")
    except Exception as e:
        st.error(f"加载消息日志失败：{e}")
        st.info("请确认已安装依赖并正确配置 .env 文件。")
