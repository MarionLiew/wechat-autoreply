from __future__ import annotations

"""需人工处理判定 + 邮件提醒。

触发条件：客户消息里出现"选座 / 改签 / 退票 / 升舱 / 查里程 / 帮我办..."等
bot 实际办不了、必须本人操作的关键词。

throttle：同一客户 10 分钟内只发一封邮件（避免连续消息刷屏）。
"""

import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# 需人工处理的关键词（任一命中即触发）。
# 命中即视为"需要真人手动操作"，bot 的回复只是先安抚。
_MANUAL_ACTION_RE = re.compile(
    # 选座 / 换座（含口语："选个座"、"选第几排"、"选个靠窗"等）
    r"选[座位]|选个[座位]|选第.{1,3}排|换[座位]|靠窗的位置|靠过道的位置|选个靠"
    # 改签退票
    r"|改签|退票|退款|退订"
    # 升舱降舱改舱（含"升个舱"）
    r"|升.{0,2}舱|改.{0,2}舱|降.{0,2}舱"
    # 改时间/换航班
    r"|改时间|改日期|换时间|换日期|换航班|改航班|换班机"
    # 取消订单
    r"|取消(?:订单|预订|机票|订位)"
    # 值机
    r"|值机|办理值机|帮我值"
    # 里程
    r"|查里程|兑里程|补里程|领里程|加里程|累计里程|里程.{0,5}(?:没|未|不|忘|少|漏)"
    # 开发票
    r"|开发票|发票"
    # 改信息
    r"|改(?:身份|证件|姓名|手机|信息)"
    # 兜底口语化求助
    r"|帮我办|帮我搞|帮我处理|帮我弄"
)

# 节流：sender → 上次提醒时间戳
_THROTTLE: dict[str, float] = {}
_THROTTLE_SECONDS = 10 * 60  # 10 分钟


def needs_manual_handling(customer_message: str) -> str | None:
    """如果消息匹配人工处理关键词，返回命中的子串；否则 None。"""
    if not customer_message:
        return None
    m = _MANUAL_ACTION_RE.search(customer_message)
    return m.group(0) if m else None


def maybe_alert(
    sender: str, customer_message: str, bot_reply: str,
    force_keyword: str | None = None,
) -> bool:
    """检测 + 发邮件（带节流）。返回是否真发出了邮件。

    sender:           客户标识（如 '周斌(男)-1234'）
    customer_message: 客户原话（多条合并文本）
    bot_reply:        bot 即将/已发出的回复内容
    force_keyword:    跳过关键词正则、直接当命中处理（例如企微内置 AI 自己
                       判断"这条需要转人工"时传入，作为正则漏判场景的兜底——
                       两条信号独立，任一命中都提醒）。为 None 时走原逻辑。
    """
    keyword = force_keyword or needs_manual_handling(customer_message)
    if not keyword:
        return False

    now = time.time()
    last = _THROTTLE.get(sender, 0)
    if now - last < _THROTTLE_SECONDS:
        logger.info(
            "[需人工] [%s] 命中 %r 但 %.0fs 内已提醒过，跳过本次邮件",
            sender, keyword, now - last,
        )
        return False
    _THROTTLE[sender] = now

    # 延迟 import 避免循环依赖
    from config import settings
    from storage import mailer

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = f"[WeCom 待处理] {sender} → {keyword}"
    body = (
        f"⚠ 客户消息命中【{keyword}】，需要你手动处理。\n"
        f"\n"
        f"客户：     {sender}\n"
        f"时间：     {ts}\n"
        f"\n"
        f"客户消息：\n"
        f"  {customer_message}\n"
        f"\n"
        f"bot 已自动回复（仅安抚，未真正执行操作）：\n"
        f"  {bot_reply}\n"
        f"\n"
        f"请尽快在企微里完成操作并回复客户。\n"
    )

    if not settings.smtp_user or not settings.smtp_password or not settings.smtp_to:
        logger.warning("[需人工] SMTP 未配置，仅打日志（命中 %r 客户 %s）", keyword, sender)
        return False

    ok = mailer.send_mail(subject, body)
    if ok:
        logger.info("📧 [需人工] 已发邮件提醒：%s → %s", sender, keyword)
    return ok
