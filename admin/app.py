"""Streamlit 管理界面：规则管理 + 消息日志。"""

import json
from pathlib import Path

import streamlit as st

RULES_FILE = Path(__file__).parent.parent / "rules.json"

st.set_page_config(page_title="企业微信自动回复 - 管理后台", layout="wide")
st.title("企业微信自动回复 管理后台")

tab_rules, tab_logs = st.tabs(["回复规则", "消息日志"])

# ──────────────────────────────────────────────
# Tab 1: 规则管理
# ──────────────────────────────────────────────
with tab_rules:
    st.subheader("回复规则配置")

    def load_rules_data() -> dict:
        with open(RULES_FILE, encoding="utf-8") as f:
            return json.load(f)

    def save_rules_data(data: dict) -> None:
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    rules_data = load_rules_data()
    rules = rules_data.get("rules", [])

    # 显示现有规则
    for i, rule in enumerate(rules):
        with st.expander(
            f"{'✅' if rule.get('enabled') else '❌'} [{rule.get('match_type','?')}] "
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
                new_reply = st.text_area("回复内容", value=rule.get("reply", ""), key=f"reply_{i}")
                new_priority = st.number_input("优先级（越小越高）", value=rule.get("priority", 99), step=1, key=f"pri_{i}")
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
                    )
                    rules_data["rules"] = rules
                    save_rules_data(rules_data)
                    st.success("已保存")
                if st.button("删除", key=f"del_{i}", type="secondary"):
                    rules.pop(i)
                    rules_data["rules"] = rules
                    save_rules_data(rules_data)
                    st.rerun()

    st.divider()
    st.subheader("新增规则")
    with st.form("new_rule"):
        c1, c2 = st.columns(2)
        n_name = c1.text_input("规则名称")
        n_match = c2.selectbox("匹配方式", ["exact", "contains", "regex"])
        n_kw = st.text_input("关键词 / 正则表达式")
        n_reply = st.text_area("回复内容")
        n_priority = st.number_input("优先级", value=len(rules) + 1, step=1)
        if st.form_submit_button("添加规则"):
            import uuid
            new_rule = {
                "id": f"rule_{uuid.uuid4().hex[:6]}",
                "enabled": True,
                "name": n_name,
                "match_type": n_match,
                "keyword": n_kw if n_match != "regex" else None,
                "pattern": n_kw if n_match == "regex" else None,
                "reply": n_reply,
                "priority": int(n_priority),
            }
            rules.append(new_rule)
            rules_data["rules"] = rules
            save_rules_data(rules_data)
            st.success("规则已添加")
            st.rerun()

# ──────────────────────────────────────────────
# Tab 2: 消息日志
# ──────────────────────────────────────────────
with tab_logs:
    st.subheader("消息日志（最近 200 条）")

    try:
        from storage import message_log
        message_log.init_db()
        logs = message_log.get_recent_logs(limit=200)

        if not logs:
            st.info("暂无消息记录。启动 run.py 并收到消息后将在此显示。")
        else:
            import pandas as pd

            df = pd.DataFrame(
                [
                    {
                        "时间": log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                        "客户": log.customer_id,
                        "消息": log.message,
                        "回复": log.reply,
                        "来源": log.source,
                    }
                    for log in logs
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)

            col1, col2, col3 = st.columns(3)
            col1.metric("总消息数", len(logs))
            rules_count = sum(1 for l in logs if l.source == "rules")
            col2.metric("规则命中", rules_count)
            col3.metric("Claude 兜底", len(logs) - rules_count)
    except Exception as e:
        st.error(f"加载消息日志失败：{e}")
        st.info("请确认已安装依赖并正确配置 .env 文件。")
